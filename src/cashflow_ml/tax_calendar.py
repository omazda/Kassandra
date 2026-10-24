"""X7: tax calendar. Tax regime from history and ENP events (monthly part + quarterly advances).

Everything is computed from operations with operation_date <= as_of. The calendar plugs into the
ENP mechanism of X2 through the ``tax_calendar`` argument of ``detect_mechanisms``: when the regime
is known and the advance rate is learnt, the calendar is the single owner of the ENP events and they
get ``mechanism == "tax_calendar"``. Otherwise the X2 default ENP events are kept (``enp``).

Model (amounts in kopecks, integers):
  ENP(month) = base + sum over charge periods of month ( rate_eff * revenue(period) ) + residual
  base      median of the last 3 core months (months without a regime charge, one-offs removed) or share of the previous payroll
            (payroll taxes) when that share is steadier on history;
  rate_eff  median of (month total - base) / revenue(period) over past charge months of the
            company, i.e. the nominal rate after deductions;
  residual  what was left in the same month last year after the structure (fixed contributions,
            one-percent payments); scaled by base growth.
Charge months: USN 4 / 7 / 10 (quarters 1-3), annual settlement (3 for an LLC, 4 for a sole
proprietor, revenue of quarter 4); ESKHN 7 (half-year) and 3 (second half). Dates: the 28th moved to
the next working day (calendar_ru).
"""

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from cashflow_domain.schemas import is_revenue
from cashflow_ml.calendar_ru import nominal_date

SOURCE = "tax_calendar"
RECIPE_VERSION = "tax-calendar-v1"
REGIMES = ("usn_6", "usn_15", "osno", "esxn", "unknown")
REGIME_LABELS = {
    "usn_6": "УСН «доходы»",
    "usn_15": "УСН «доходы минус расходы»",
    "osno": "ОСНО",
    "esxn": "ЕСХН",
    "unknown": "режим не определён",
}
ORDINARY_MONTHS = (2, 5, 8, 11)  # no quarterly or annual charge
SPIKE = 1.25  # month total / base that counts as a charge
NO_CHARGE = 1.15  # a charge month below this ratio last year is treated as charge-free
ENFORCEMENT_RE = re.compile(r"поручению налогового|взыскан", re.IGNORECASE)
NON_COST = {"tax", "owner_funding", "loan_in", "own_transfer", "owner_withdrawal", "debt_service"}
MIN_HISTORY_DAYS = 270
DEFAULT_ADVANCE_MONTHS = (4, 7, 10)  # as in X2 DefaultTaxCalendar
BASE_MONTHS = 3  # latest core months in the base median
ONE_OFF = 2.5  # a plain month above this many medians is a one-off payment
RESIDUAL_CAP = 3.0  # a residual above this many bases is a one-off, not a recurring item


# --------------------------------------------------------------------------- helpers


def _month_after(year: int, month: int, n: int = 1) -> tuple[int, int]:
    index = year * 12 + month - 1 + n
    return index // 12, index % 12 + 1


def _month_end(year: int, month: int) -> date:
    return date(*_month_after(year, month), 1) - timedelta(days=1)


def _is_enp(t: dict) -> bool:
    return (
        t["category"] == "tax"
        and t["direction"] == "out"
        and not ENFORCEMENT_RE.search(t.get("payment_purpose") or "")
    )


def nominal_month(d: date) -> tuple[int, int]:
    """(year, month) of the ENP a payment on d belongs to: 1-4 of the month is the carried 28th."""
    pm = _month_after(d.year, d.month, -1)
    due_prev = nominal_date(pm[0], pm[1], 28, "next")
    return pm if d.month != pm[1] and d <= due_prev + timedelta(days=1) else (d.year, d.month)


def charge_periods(
    regime: str, legal_form: str | None, year: int, month: int
) -> list[tuple[date, date]]:
    """Revenue periods the ENP of (year, month) settles, by tax regime."""
    q = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}

    def quarter(y: int, n: int) -> tuple[date, date]:
        a, b = q[n]
        return date(y, a, 1), _month_end(y, b)

    out: list[tuple[date, date]] = []
    if regime in ("usn_6", "usn_15"):
        by_month = {4: 1, 7: 2, 10: 3}
        if month in by_month:
            out.append(quarter(year, by_month[month]))
        annual_month = 4 if legal_form == "ИП" else 3
        if month == annual_month:
            out.append(quarter(year - 1, 4))
    elif regime == "esxn":
        if month == 7:
            out.append((date(year, 1, 1), _month_end(year, 6)))
        if month == 3:
            out.append((date(year - 1, 7, 1), _month_end(year - 1, 12)))
    return out


@dataclass(frozen=True)
class TaxRegime:
    regime: str  # usn_6 | usn_15 | osno | esxn | unknown
    confidence: float
    evidence: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return REGIME_LABELS[self.regime]

    def to_dict(self) -> dict:
        return {"regime": self.regime, "confidence": self.confidence, "evidence": self.evidence}


class _History:
    """Monthly ENP totals, payroll, revenue by day, built from operations <= as_of only."""

    def __init__(self, transactions: list[dict], as_of: date):
        limit = as_of.isoformat()
        self.as_of = as_of
        self.enp_month: dict[tuple[int, int], int] = defaultdict(int)
        self.enp_days: list[tuple[date, int]] = []
        self.salary_month: dict[tuple[int, int], int] = defaultdict(int)
        self.revenue: dict[date, int] = defaultdict(int)
        self.cost: dict[date, int] = defaultdict(int)
        first = None
        for t in transactions:
            if t["operation_date"] > limit:
                continue
            d = date.fromisoformat(t["operation_date"])
            first = d if first is None or d < first else first
            amount = t["amount_minor"]
            if _is_enp(t):
                self.enp_month[nominal_month(d)] += amount
                self.enp_days.append((d, amount))
            elif t["category"] == "salary" and t["direction"] == "out":
                self.salary_month[(d.year, d.month)] += amount
            elif is_revenue(t["category"], t["direction"]):
                self.revenue[d] += amount
            elif t["direction"] == "out" and t["category"] not in NON_COST:
                self.cost[d] += amount
        self.start = first or as_of
        self.core: list[tuple[int, int]] | None = None  # months with the monthly part only
        # the first history month is complete only if the history begins on its first day
        self.months = [
            ym
            for ym in sorted(self.enp_month)
            if _month_end(*ym) <= as_of and date(ym[0], ym[1], 1) >= self.start
        ]

    def span_days(self) -> int:
        return (self.as_of - self.start).days + 1

    def ordinary(self) -> list[tuple[int, int]]:
        return [ym for ym in self.months if ym[1] in ORDINARY_MONTHS]

    def base_local(self, ym: tuple[int, int]) -> int | None:
        """Median monthly part within three months of ym: core months once known, else 2/5/8/11."""
        idx = ym[0] * 12 + ym[1]
        pool = self.core if self.core else self.ordinary()
        near = [self.enp_month[o] for o in pool if abs(o[0] * 12 + o[1] - idx) <= 3] or [
            self.enp_month[o] for o in pool
        ]
        return int(statistics.median(near)) if near else None

    def revenue_in(self, a: date, b: date) -> float | None:
        """Revenue in [a, b]; the part after as_of is extrapolated by the last 28 days."""
        if a < self.start:
            return None
        known_end = min(b, self.as_of)
        total = float(sum(v for d, v in self.revenue.items() if a <= d <= known_end))
        if b > self.as_of:
            recent = sum(v for d, v in self.revenue.items() if self.as_of - timedelta(days=27) <= d)
            total += recent / 28 * (b - max(a - timedelta(days=1), self.as_of)).days
        return total

    def cost_in(self, a: date, b: date) -> float:
        return float(sum(v for d, v in self.cost.items() if a <= d <= min(b, self.as_of)))


# --------------------------------------------------------------------------- regime inference


def _charge_events(h: _History, regime: str, legal_form: str | None):
    """(ym, excess, revenue of periods, net of periods) for completed charge months in history."""
    out = []
    for ym in h.months:
        periods = charge_periods(regime, legal_form, *ym)
        base = h.base_local(ym)
        if not periods or base is None or ym[1] in ORDINARY_MONTHS:
            continue
        revs = [h.revenue_in(a, b) for a, b in periods]
        if any(r is None for r in revs) or any(b > h.as_of for _, b in periods):
            continue
        rev = sum(revs)
        cost = sum(h.cost_in(a, b) for a, b in periods)
        if rev <= 0:
            continue
        out.append((ym, h.enp_month[ym] - base, rev, rev - cost))
    return out


def infer_regime(transactions: list[dict], as_of: date, legal_form: str | None = None) -> TaxRegime:
    """Tax regime from ENP rhythm, payroll share and revenue; history <= as_of only."""
    return _infer(_History(transactions, as_of), legal_form)


def _infer(h: _History, legal_form: str | None) -> TaxRegime:
    ordinary = h.ordinary()
    ev = {"history_days": h.span_days(), "ordinary_months": len(ordinary)}
    if h.span_days() < MIN_HISTORY_DAYS or len(ordinary) < 2:
        return TaxRegime("unknown", 0.0, {**ev, "reason": "history too short"})
    ref = statistics.median(h.enp_month[o] for o in ordinary)
    if ref <= 0:
        return TaxRegime("unknown", 0.0, {**ev, "reason": "no regular ENP"})
    ratio: dict[int, float] = {}  # latest complete observation per month of year
    for ym in h.months:
        ratio[ym[1]] = h.enp_month[ym] / ref
    ev["ratio_by_month"] = {str(m): round(r, 2) for m, r in sorted(ratio.items())}
    quarter_obs = [m for m in (4, 7, 10) if m in ratio]
    quarter_hits = [m for m in quarter_obs if ratio[m] >= SPIKE]
    usn_score = len(quarter_hits) / len(quarter_obs) if len(quarter_obs) >= 2 else None
    ev["quarter_months_observed"] = quarter_obs
    ev["quarter_months_with_charge"] = quarter_hits
    # payroll taxes (NDFL + contributions) are about 43% of the payroll
    pairs = [
        h.enp_month[o] / h.salary_month[_month_after(*o, -1)]
        for o in ordinary
        if h.salary_month.get(_month_after(*o, -1))
    ]
    payroll_ratio = statistics.median(pairs) if len(pairs) >= 3 else None
    ev["enp_to_payroll_ratio"] = None if payroll_ratio is None else round(payroll_ratio, 3)

    if (
        7 in ratio
        and 10 in ratio
        and ratio[7] >= SPIKE
        and ratio[10] < 1.15
        and (ratio.get(4, 1.0) < SPIKE)
    ):
        n = len([m for m in (3, 7, 10) if m in ratio])
        return TaxRegime("esxn", round(min(0.9, 0.5 + 0.1 * n), 2), {**ev, "rule": "7 only"})
    if usn_score is not None and usn_score >= 0.6:
        regime, extra = _usn_subtype(h, legal_form)
        n_events = extra["n_charge_events"]
        conf = min(0.99, usn_score * min(1.0, 0.4 + 0.2 * len(quarter_obs)))
        if n_events == 0:
            conf *= 0.7
        return TaxRegime(regime, round(conf, 3), {**ev, **extra, "rule": "quarterly charges"})
    if usn_score is not None and payroll_ratio is not None and payroll_ratio > 0.7:
        return TaxRegime("osno", 0.5, {**ev, "rule": "no quarterly charges, ENP above payroll"})
    return TaxRegime("unknown", 0.0, {**ev, "reason": "no clear pattern"})


def _usn_subtype(h: _History, legal_form: str | None) -> tuple[str, dict]:
    events = _charge_events(h, "usn_6", legal_form)
    extra = {"n_charge_events": len(events)}
    if len(events) < 2:
        return "usn_6", extra
    on_rev = [e[1] / e[2] for e in events]
    on_net = [e[1] / e[3] for e in events if e[3] > 0]
    rate_rev = statistics.median(on_rev)
    extra["median_excess_to_revenue"] = round(rate_rev, 4)
    if len(on_net) == len(events) and statistics.fmean(on_net) > 0:
        rate_net = statistics.median(on_net)
        extra["median_excess_to_net"] = round(rate_net, 4)
        cv_rev = _cv(on_rev)
        cv_net = _cv(on_net)
        extra["cv_revenue"], extra["cv_net"] = round(cv_rev, 3), round(cv_net, 3)
        if cv_net < 0.8 * cv_rev and 0.08 <= rate_net <= 0.2:
            return "usn_15", extra
    return "usn_6", extra


def _cv(values: list[float]) -> float:
    mean = statistics.fmean(values)
    return statistics.pstdev(values) / abs(mean) if mean else float("inf")


# --------------------------------------------------------------------------- the calendar


class RegimeTaxCalendar:
    """TaxCalendar (X2 protocol) with a regime; ``forecast`` gives ENP events for the window."""

    due_day = 28

    def __init__(self, transactions: list[dict], as_of: date, legal_form: str | None = None):
        self.as_of = as_of
        self.legal_form = legal_form
        self._h = _History(transactions, as_of)
        self.regime = _infer(self._h, legal_form)
        self._learn()

    # protocol ------------------------------------------------------------------------
    def due_date(self, year: int, month: int) -> date:
        return nominal_date(year, month, self.due_day, "next")

    def is_advance_month(self, month: int) -> bool:
        if not self.active:  # defer to X2: its default advance months
            return month in DEFAULT_ADVANCE_MONTHS
        probe = 2030  # any year: the charge months depend only on the month
        return bool(charge_periods(self.regime.regime, self.legal_form, probe, month))

    # learning ------------------------------------------------------------------------
    def _learn(self) -> None:
        h = self._h
        self.rate: float | None = None
        self.base: int | None = None
        self.payroll_share: float | None = None
        self.dom_window: tuple[int, int] | None = None
        self.on_due = 1.0
        self.params: dict = {}
        ordinary = h.ordinary()
        if self.regime.regime == "unknown" or len(ordinary) < 2:
            return
        # core months: no charge of the regime and no one-off (>= 2.5 x median of such months)
        plain = [
            ym
            for ym in h.months
            if not charge_periods(self.regime.regime, self.legal_form, 2030, ym[1])
        ]
        if len(plain) >= 3:
            ref = statistics.median(h.enp_month[ym] for ym in plain)
            h.core = [ym for ym in plain if h.enp_month[ym] < ONE_OFF * ref]
        core = h.core or ordinary
        self.base = int(statistics.median(h.enp_month[o] for o in core[-BASE_MONTHS:]))
        pairs = [
            (h.enp_month[o] / h.salary_month[_month_after(*o, -1)], h.enp_month[o])
            for o in core
            if h.salary_month.get(_month_after(*o, -1))
        ]
        if len(pairs) >= 4:
            ratios, amounts = [p[0] for p in pairs], [p[1] for p in pairs]
            if _cv(ratios) < 0.8 * _cv(amounts):
                self.payroll_share = statistics.median(ratios[-3:])
        events = _charge_events(h, self.regime.regime, self.legal_form)
        if events:
            rates = [e[1] / e[2] for e in events][-4:]
            self.rate = max(0.0, statistics.median(rates))
        # days of month of the ordinary payments: how reliably they land on the due date
        doms, hits = [], 0
        for d, _amount in h.enp_days:
            ym = nominal_month(d)
            if ym in ordinary:
                doms.append(d.day if (d.year, d.month) == ym else 28)
                hits += abs((d - self.due_date(*ym)).days) <= 3
        self.on_due = hits / len(doms) if doms else 1.0
        if doms and self.on_due < 0.7:
            qs = statistics.quantiles(doms, n=10, method="inclusive") if len(doms) > 2 else doms
            self.dom_window = (int(qs[0]), int(qs[-1]))
            self.params["median_dom"] = int(statistics.median(doms))
        self.params.update(
            base_minor=self.base,
            rate_eff=None if self.rate is None else round(self.rate, 5),
            payroll_share=None if self.payroll_share is None else round(self.payroll_share, 4),
            on_due_share=round(self.on_due, 3),
            n_charge_events=len(events),
        )

    @property
    def active(self) -> bool:
        """True when the calendar can size every charge of its regime (otherwise X2 forecasts)."""
        if self.base is None:
            return False
        if self.regime.regime in ("usn_6", "usn_15", "esxn"):
            return self.rate is not None
        return True

    # amounts -------------------------------------------------------------------------
    def _base_now(self, year: int, month: int) -> float:
        h = self._h
        base = float(self.base)
        pm = _month_after(year, month, -1)
        if self.payroll_share and h.salary_month.get(pm) and _month_end(*pm) <= self.as_of:
            base = self.payroll_share * h.salary_month[pm]
        return base

    def _structural(self, year: int, month: int) -> float | None:
        periods = charge_periods(self.regime.regime, self.legal_form, year, month)
        if not periods:
            return 0.0
        ly = (year - 1, month)
        base_ly = self._h.base_local(ly) if ly in self._h.months else None
        if base_ly and self._h.enp_month[ly] < NO_CHARGE * base_ly:
            return 0.0  # this month showed no charge a year ago: the regime rule does not apply
        revs = [self._h.revenue_in(a, b) for a, b in periods]
        if any(r is None for r in revs):
            return None
        return self.rate * sum(revs)

    def month_amount(self, year: int, month: int) -> float | None:
        base = self._base_now(year, month)
        structural = self._structural(year, month)
        if structural is None:
            return None
        amount = base + structural
        # residual of the same month last year: fixed contributions, one-percent payments
        ly = (year - 1, month)
        h = self._h
        if ly in h.months:
            base_ly = h.base_local(ly)
            struct_ly = self._structural(*ly)
            if base_ly and struct_ly is not None:
                residual = h.enp_month[ly] - base_ly - struct_ly
                if 0.25 * base_ly < residual <= RESIDUAL_CAP * base_ly:
                    amount += residual * min(max(base / base_ly, 0.5), 2.0)
        return amount

    def forecast(self, as_of: date, end: date):
        """[(date, amount_minor, window or None)] for due dates in (as_of, end]; None = defer to X2."""
        if as_of != self.as_of:
            raise ValueError("the calendar was built for another as_of")
        if not self.active:
            return None
        out = []
        y, m = _month_after(as_of.year, as_of.month, -1)
        while (y, m) <= (end.year, end.month):
            due = self.due_date(y, m)
            if as_of < due <= end:
                amount = self.month_amount(y, m)
                if amount is None:
                    return None
                paid = sum(
                    a for d, a in self._h.enp_days if nominal_month(d) == (y, m) and d <= as_of
                )
                amount -= paid
                if amount > 0.25 * self._base_now(y, m):
                    event_date, window = self._place(y, m, due)
                    out.append((event_date, int(round(amount)), window))
            y, m = _month_after(y, m)
        return out

    def _place(self, y: int, m: int, due: date) -> tuple[date, tuple[date, date] | None]:
        if self.dom_window is None:
            return due, None
        lo, hi = self.dom_window
        window = (
            nominal_date(y, m, min(lo, 28), "next"),
            nominal_date(y, m, min(max(hi, lo), 28), "next"),
        )
        if self.on_due >= 0.5:
            return due, window
        return nominal_date(y, m, min(self.params["median_dom"], 28), "next"), window

    def to_dict(self) -> dict:
        return {
            "recipe": RECIPE_VERSION,
            "as_of": self.as_of.isoformat(),
            "legal_form": self.legal_form,
            "regime": self.regime.to_dict(),
            "active": self.active,
            "params": self.params,
        }


def build_tax_calendar(
    transactions: list[dict], as_of: date, legal_form: str | None = None
) -> RegimeTaxCalendar:
    return RegimeTaxCalendar(transactions, as_of, legal_form)


def tax_events(
    transactions: list[dict],
    as_of: date,
    legal_form: str | None = None,
    horizon: int = 30,
    use_season: bool = False,
):
    """ENP events for the next `horizon` days from the single owner of ENP.

    Returns (events, calendar). Events with mechanism "tax_calendar" come from the regime
    calendar; if it cannot size the charges, the X2 events (mechanism "enp") stay.
    """
    from cashflow_ml.mechanisms import detect_mechanisms

    cal = build_tax_calendar(transactions, as_of, legal_form)
    model = detect_mechanisms(transactions, as_of, tax_calendar=cal, use_season=use_season)
    events = [e for e in model.events(horizon) if e.category == "tax"]
    return events, cal


def naive_median_forecast(transactions: list[dict], as_of: date, horizon: int = 30):
    """Baseline: median of the last three ENP payments, on the median day of month of the three."""
    rows = sorted(
        (date.fromisoformat(t["operation_date"]), t["amount_minor"])
        for t in transactions
        if t["operation_date"] <= as_of.isoformat() and _is_enp(t)
    )
    if len(rows) < 3:
        return []
    last = rows[-3:]
    amount = int(statistics.median(a for _, a in last))
    day = int(statistics.median(d.day for d, _ in last))
    out = []
    y, m = _month_after(as_of.year, as_of.month, -1)
    end = as_of + timedelta(days=horizon)
    while (y, m) <= (end.year, end.month):
        d = nominal_date(y, m, min(day, 28) if day > 28 else day, "next")
        if as_of < d <= end:
            out.append((d, amount))
        y, m = _month_after(y, m)
    return out


__all__ = [
    "SOURCE",
    "REGIMES",
    "TaxRegime",
    "RegimeTaxCalendar",
    "build_tax_calendar",
    "infer_regime",
    "tax_events",
    "naive_median_forecast",
    "charge_periods",
]
