"""Mechanism detectors (D5): structural expected events from history <= as_of.

`detect_mechanisms(transactions, as_of)` looks only at operations with operation_date <= as_of
(later rows are dropped defensively) and returns a `MechanismModel`. `model.events(30)` lists the
expected events on as_of+1 .. as_of+30 (date, amount in kopecks, direction, category, mechanism,
component_id, counterparty, confidence). `ExpectedEvent.to_planned_event()` converts one into a
`PlannedEvent` (source "regular", status "assumption").

Ownership (needed by X4 to take the history out of the residual flow without double counting):
an operation belongs to a *group* decided by `group_key(tx)` from category, direction and
counterparty (or contract number for loans) only:

    salary|out                       every salary operation of the account
    tax|out                          every tax operation (ENP and recoveries)
    <fixed category>|out|<cp>        rent, utilities, telecom, software, accounting,
                                     facility_services, bank_fee per counterparty
    debt_service|out|<contract|cp>   credit / leasing per contract number (КД-, ЛД-) else cp
    acquiring|in, sbp|in, cash_in|in the whole channel of the account

A group is *owned by the model* only when a component was detected for it at as_of
(`model.owns(tx)`). The rule is a function of the operation alone, so it applies equally to
history (to subtract from the residual) and to future operations (to evaluate). Operations of an
owned group that lie off the component pattern (an extra tax payment, a bonus) are still owned:
they are removed from the residual flow and not predicted.
"""

import hashlib
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Protocol

from cashflow_domain.schemas import CATEGORY_LABELS, PlannedEvent
from cashflow_ml.calendar_ru import (
    add_working_days,
    is_working_day,
    next_working_day,
    nominal_date,
    prev_working_day,
    working_days_between,
)

RECIPE_VERSION = "mechanisms-v1"

# Transfer rules, determined from the archive (experiments/results/v3/mechanisms/rules.json):
# salary is paid on the previous working day, tax / rent / loans / fees on the next one.
SALARY_RULE = "prev"
MONTHLY_RULE = "next"

FIXED_CATEGORIES = (
    "rent",
    "utilities",
    "telecom",
    "software",
    "accounting",
    "facility_services",
    "bank_fee",
)
SALES_CATEGORIES = ("acquiring", "sbp", "cash_in")
CONTRACT_RE = re.compile(r"\b((?:КД|ЛД)-\d+)")
DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


# --------------------------------------------------------------------------- public types


class TaxCalendar(Protocol):
    """Hook for the tax calendar (X7): due dates and months with advance payments."""

    def due_date(self, year: int, month: int) -> date: ...

    def is_advance_month(self, month: int) -> bool: ...


class DefaultTaxCalendar:
    """ENP is due on the 28th, moved to the next working day; USN/VAT advances in 4, 7, 10."""

    due_day = 28
    advance_months = (4, 7, 10)

    def due_date(self, year: int, month: int) -> date:
        return nominal_date(year, month, self.due_day, "next")

    def is_advance_month(self, month: int) -> bool:
        return month in self.advance_months


@dataclass(frozen=True)
class ExpectedEvent:
    date: date
    amount_minor: int
    direction: str
    category: str
    mechanism: str  # ... | enp | tax_calendar (X7, replaces enp) | ...
    component_id: str
    group_key: str
    counterparty: str
    confidence: float
    window: tuple[date, date] | None = None  # plausible date range when the day jitters

    def to_planned_event(self, account_id: str, as_of: date) -> PlannedEvent:
        return PlannedEvent(
            id=f"mech:{account_id}:{self.component_id}:{self.date}",
            account_id=account_id,
            component_id=self.component_id,
            category=self.category,
            amount_minor=self.amount_minor,
            direction=self.direction,
            due_date=self.date,
            expected_date=self.date,
            known_at=as_of,
            source="regular",
            status="assumption",
            label=f"{CATEGORY_LABELS[self.category]} · {self.mechanism}",
        )


@dataclass(frozen=True)
class Component:
    component_id: str
    mechanism: str  # salary | enp | fixed_monthly | loan | acquiring | sbp | cash_in
    category: str
    direction: str
    group_key: str
    counterparty: str
    confidence: float
    params: dict = field(default_factory=dict)  # JSON-able


@dataclass
class MechanismModel:
    as_of: date
    components: list[Component]
    # Per-component private state that is not JSON-able (sales profiles), keyed by component_id.
    _state: dict = field(default_factory=dict, repr=False)

    @property
    def groups(self) -> set[str]:
        return {c.group_key for c in self.components}

    def owns(self, tx: dict) -> bool:
        return group_key(tx) in self.groups

    def assign(self, tx: dict) -> str | None:
        """component_id of the component the operation belongs to (nearest cluster for salary)."""
        key = group_key(tx)
        comps = [c for c in self.components if c.group_key == key]
        if not comps:
            return None
        if len(comps) == 1:
            return comps[0].component_id
        day = int(tx["operation_date"][8:10])
        return min(comps, key=lambda c: abs(c.params.get("day", day) - day)).component_id

    def owned_operation_ids(self, transactions: list[dict], until: date | None = None) -> set[str]:
        """Operation ids (<= `until`, default as_of) that belong to detected components."""
        limit = (until or self.as_of).isoformat()
        return {
            t["operation_id"]
            for t in transactions
            if t["operation_date"] <= limit and group_key(t) in self.groups
        }

    def events(self, horizon: int = 30) -> list[ExpectedEvent]:
        out: list[ExpectedEvent] = []
        for comp in self.components:
            out.extend(
                _component_events(comp, self.as_of, horizon, self._state.get(comp.component_id))
            )
        return sorted(out, key=lambda e: (e.date, e.component_id))

    def planned_events(self, account_id: str, horizon: int = 30) -> list[PlannedEvent]:
        return [e.to_planned_event(account_id, self.as_of) for e in self.events(horizon)]

    def to_dict(self) -> dict:
        return {
            "recipe": RECIPE_VERSION,
            "as_of": self.as_of.isoformat(),
            "components": [
                {k: getattr(c, k) for k in c.__dataclass_fields__} for c in self.components
            ],
        }


# --------------------------------------------------------------------------- grouping


def counterparty_key(tx: dict) -> str:
    return tx.get("counterparty_inn") or tx.get("counterparty_name") or ""


def group_key(tx: dict) -> str | None:
    category, direction = tx["category"], tx["direction"]
    if direction == "out":
        if category == "salary":
            return "salary|out"
        if category == "tax":
            return "tax|out"
        if category in FIXED_CATEGORIES:
            return f"{category}|out|{counterparty_key(tx)}"
        if category == "debt_service":
            match = CONTRACT_RE.search(tx.get("payment_purpose") or "")
            return f"debt_service|out|{match.group(1) if match else counterparty_key(tx)}"
    elif direction == "in" and category in SALES_CATEGORIES:
        return f"{category}|in"
    return None


def _cid(prefix: str, key: str) -> str:
    return f"{prefix}:{hashlib.sha1(key.encode()).hexdigest()[:8]}" if key else prefix


# --------------------------------------------------------------------------- date helpers


def _month_after(year: int, month: int, n: int = 1) -> tuple[int, int]:
    index = year * 12 + month - 1 + n
    return index // 12, index % 12 + 1


def _months_covering(first: date, last: date) -> list[tuple[int, int]]:
    out, (y, m) = [], (first.year, first.month)
    while (y, m) <= (last.year, last.month):
        out.append((y, m))
        y, m = _month_after(y, m)
    return out


def _complete_months(as_of: date, n: int, hist_start: date) -> list[tuple[int, int]]:
    """Last n calendar months that ended on or before as_of and began on or after hist_start."""
    end_of_month = _month_after(as_of.year, as_of.month)
    last_day = date(*end_of_month, 1) - timedelta(days=1)
    y, m = (
        (as_of.year, as_of.month)
        if as_of == last_day
        else _month_after(as_of.year, as_of.month, -1)
    )
    months = []
    for _ in range(n):
        if date(y, m, 1) >= hist_start:
            months.append((y, m))
        y, m = _month_after(y, m, -1)
    return months[::-1]


def _candidate_days(d: date, rule: str) -> set[int]:
    """Day-of-month values k for which the nominal date moved by `rule` equals d."""
    if not is_working_day(d) and rule != "none":
        return set()
    out = {d.day}
    step = -1 if rule == "next" else 1
    if rule == "none":
        return out
    cur = d + timedelta(days=step)
    while not is_working_day(cur):
        out.add(cur.day)
        cur += timedelta(days=step)
    return out


def fit_nominal_days(
    days, rule: str, max_clusters: int, min_hits: int
) -> list[tuple[int, list[date]]]:
    """Greedy cover of observed dates by nominal days-of-month under a transfer rule."""
    remaining = set(days)
    clusters = []
    for _ in range(max_clusters):
        votes: Counter = Counter()
        cand = {d: _candidate_days(d, rule) for d in remaining}
        for ks in cand.values():
            votes.update(ks)
        if not votes:
            break
        k, count = max(votes.items(), key=lambda kv: (kv[1], -kv[0]))
        if count < min_hits:
            break
        hits = sorted(d for d, ks in cand.items() if k in ks)
        clusters.append((k, hits))
        remaining -= set(hits)
    return clusters


def _hit_rate(obs: set[date], nominal_dates: list[date]) -> float:
    if not nominal_dates:
        return 0.0
    hit = 0
    for nd in nominal_dates:
        near = {nd, add_working_days(nd, -1), add_working_days(nd, 1)}
        hit += bool(near & obs)
    return hit / len(nominal_dates)


def _median_int(values: list[int]) -> int:
    return int(statistics.median(values))


# --------------------------------------------------------------------------- detection


def detect_mechanisms(
    transactions: list[dict],
    as_of: date,
    tax_calendar: TaxCalendar | None = None,
    use_season: bool = True,
) -> MechanismModel:
    tax_calendar = tax_calendar or DefaultTaxCalendar()
    limit = as_of.isoformat()
    history = [t for t in transactions if t["operation_date"] <= limit]
    model = MechanismModel(as_of=as_of, components=[])
    if not history:
        return model
    hist_start = date.fromisoformat(min(t["operation_date"] for t in history))
    groups: dict[str, list[tuple[date, int, dict]]] = defaultdict(list)
    for t in history:
        key = group_key(t)
        if key:
            groups[key].append((date.fromisoformat(t["operation_date"]), t["amount_minor"], t))

    salary_by_month: dict[tuple[int, int], int] = defaultdict(int)
    for d, amount, _ in groups.get("salary|out", []):
        salary_by_month[(d.year, d.month)] += amount

    for key, rows in sorted(groups.items()):
        rows.sort(key=lambda r: r[0])
        by_day: dict[date, int] = defaultdict(int)
        for d, amount, _ in rows:
            by_day[d] += amount
        if key == "salary|out":
            comps = _detect_salary(by_day, as_of, hist_start, rows)
        elif key == "tax|out":
            comps = _detect_enp(by_day, as_of, hist_start, rows, tax_calendar, salary_by_month)
            if hasattr(tax_calendar, "forecast"):  # X7 hook: the calendar owns the ENP amounts
                for comp in comps:
                    model._state[comp.component_id] = tax_calendar
        elif key.startswith("debt_service|"):
            comps = _detect_monthly(key, by_day, as_of, hist_start, rows, loan=True)
        elif key.split("|")[0] in FIXED_CATEGORIES:
            comps = _detect_monthly(key, by_day, as_of, hist_start, rows, loan=False)
        else:
            comps, state = _detect_sales(key, rows, as_of, use_season)
            for comp in comps:
                model._state[comp.component_id] = state
        model.components.extend(comps)
    return model


def _top_counterparty(rows) -> str:
    names = Counter(r[2].get("counterparty_name") or counterparty_key(r[2]) for r in rows)
    return names.most_common(1)[0][0] if names else ""


def _detect_salary(by_day, as_of, hist_start, rows) -> list[Component]:
    start = max(as_of - timedelta(days=180), hist_start)
    obs = {d: a for d, a in by_day.items() if d >= start}
    clusters = fit_nominal_days(obs, SALARY_RULE, max_clusters=3, min_hits=3)
    comps = []
    for k, hits in clusters:
        months = _months_covering(start, as_of)
        nominal = [
            nd for y, m in months if start <= (nd := nominal_date(y, m, k, SALARY_RULE)) <= as_of
        ]
        rate = len(hits) / max(len(nominal), 1)
        if rate < 0.5:
            continue
        amount = _median_int([obs[d] for d in hits[-3:]])
        comps.append(
            Component(
                component_id=f"salary:out:d{k:02d}",
                mechanism="salary",
                category="salary",
                direction="out",
                group_key="salary|out",
                counterparty=_top_counterparty(rows),
                confidence=round(min(rate, 0.99), 3),
                params=dict(
                    day=k,
                    rule=SALARY_RULE,
                    amount_minor=amount,
                    n_hits=len(hits),
                    last=hits[-1].isoformat(),
                ),
            )
        )
    return comps


def _detect_enp(by_day, as_of, hist_start, rows, cal, salary_by_month) -> list[Component]:
    first = max(as_of - timedelta(days=400), hist_start)
    months = _months_covering(first, as_of)
    anchors = []  # (due_date, nominal (y, m), amount)
    for y, m in months:
        due = cal.due_date(y, m)
        if first <= due <= as_of and due in by_day:
            anchors.append((due, (y, m), by_day[due]))
    recent = [(y, m) for y, m in months if first <= cal.due_date(y, m) <= as_of][-6:]
    if len(anchors) < 3 or not recent:
        return []
    due_recent = [cal.due_date(y, m) for y, m in recent]
    rate = _hit_rate({a[0] for a in anchors}, due_recent)
    if rate < 0.5:
        return []
    normal = [a for a in anchors if not cal.is_advance_month(a[1][1])]
    advance = [a for a in anchors if cal.is_advance_month(a[1][1])]
    if not normal:
        return []
    base = _median_int([a[2] for a in normal[-3:]])
    adv = _median_int([a[2] for a in advance[-2:]]) if advance else base

    # ENP as a share of the previous month's payroll, used only when it is clearly steadier than
    # the plain amount over the same history (decided on history only).
    fot_ratio = None
    pairs = []
    for _, (y, m), amount in normal:
        pm = _month_after(y, m, -1)
        if salary_by_month.get(pm):
            pairs.append(amount / salary_by_month[pm])
    amounts = [a[2] for a in normal]
    if len(pairs) >= 4 and len(amounts) >= 4:
        cv_r = statistics.pstdev(pairs) / statistics.fmean(pairs)
        cv_a = statistics.pstdev(amounts) / statistics.fmean(amounts)
        if cv_r < 0.8 * cv_a:
            fot_ratio = statistics.median(pairs[-3:])
    return [
        Component(
            component_id="enp:out",
            mechanism="enp",
            category="tax",
            direction="out",
            group_key="tax|out",
            counterparty=_top_counterparty(rows),
            confidence=round(min(rate, 0.99), 3),
            params=dict(
                base_minor=base,
                advance_minor=adv,
                advance_seen=bool(advance),
                fot_ratio=fot_ratio,
                salary_by_month={f"{y}-{m:02d}": v for (y, m), v in salary_by_month.items()},
                calendar=type(cal).__name__,
                advance_months=[m for m in range(1, 13) if cal.is_advance_month(m)],
                due_days={f"{y}-{m:02d}": cal.due_date(y, m).isoformat() for y, m in months[-1:]},
                n_anchors=len(anchors),
            ),
        )
    ]


def _detect_monthly(key, by_day, as_of, hist_start, rows, loan: bool) -> list[Component]:
    months = _complete_months(as_of, 6, hist_start)
    if len(months) < 3:
        return []
    last_obs = max(by_day)
    if (as_of - last_obs).days > (45 if loan else 62):
        return []
    per_month: dict[tuple[int, int], list[date]] = defaultdict(list)
    for d in by_day:
        per_month[(d.year, d.month)].append(d)
    present = [ym for ym in months if ym in per_month]
    if len(present) < max(3, -(-2 * len(months) // 3)):
        return []
    month_sum = {ym: sum(by_day[d] for d in per_month[ym]) for ym in present}
    amount = _median_int([month_sum[ym] for ym in present[-3:]])
    if amount <= 0:
        return []
    if loan:
        recent = [month_sum[ym] for ym in present[-3:]]
        if max(abs(a - amount) for a in recent) / amount > 0.1:
            return []  # annuity: the total instalment is constant
    first_days = [min(per_month[ym]) for ym in present]
    clusters = fit_nominal_days(first_days, MONTHLY_RULE, max_clusters=1, min_hits=3)
    fixed_day = bool(clusters) and len(clusters[0][1]) >= 0.6 * len(present)
    if loan and not fixed_day:
        return []
    category, _, cp = key.split("|", 2)
    doms = sorted(d.day for d in first_days)
    params = dict(
        amount_minor=amount,
        rule=MONTHLY_RULE,
        n_months=len(present),
        of_months=len(months),
        last=last_obs.isoformat(),
    )
    if fixed_day:
        k = clusters[0][0]
        params.update(date_model="fixed_day", day=k)
        nominal = [nd for y, m in months if (nd := nominal_date(y, m, k, MONTHLY_RULE)) <= as_of]
    else:
        center = int(statistics.median(doms))
        params.update(
            date_model="jitter",
            day=center,
            doms=doms,
            p10=doms[int(0.1 * (len(doms) - 1))],
            p90=doms[-1 - int(0.1 * (len(doms) - 1))],
        )
        nominal = [nominal_date(y, m, center, MONTHLY_RULE) for y, m in months]
    rate = _hit_rate(set(by_day), nominal)
    params["obs_days"] = sorted(d.isoformat() for d in by_day if d >= date(*months[0], 1))
    counterparty = _top_counterparty(rows)
    mechanism = "loan" if loan else "fixed_monthly"
    return [
        Component(
            component_id=_cid(f"{mechanism}:{category}", cp),
            mechanism=mechanism,
            category=category,
            direction="out",
            group_key=key,
            counterparty=counterparty,
            confidence=round(max(min(rate * len(present) / len(months), 0.99), 0.05), 3),
            params=params,
        )
    ]


# --------------------------------------------------------------------------- sales channels


@dataclass
class _SalesState:
    kind: str  # "registry" (acquiring, sbp) or "credit_day" (cash_in)
    last_sale: date
    level: dict  # weekday -> mean daily amount over the last 8 weeks (incl. zero days)
    presence: dict  # weekday -> share of the last 8 weeks with a positive amount
    season: dict  # month -> index relative to the trailing 12 months (empty = off)
    recent_season: float


def _sale_date(tx: dict, op_date: date) -> date:
    """Registry / sales date written in the purpose; falls back to the calendar day before."""
    match = DATE_RE.search(tx.get("payment_purpose") or "")
    if match:
        try:
            s = date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            s = None
        if s and op_date - timedelta(days=20) <= s < op_date:
            return s
    return op_date - timedelta(days=1)


def _season_index(daily: dict[date, int], last: date) -> dict[int, float]:
    """Month-of-year index from 12 full calendar months before `last`'s month; {} if too short."""
    if not daily:
        return {}
    first = min(daily)
    y, m = _month_after(last.year, last.month, -1)
    months = []
    for _ in range(12):
        months.append((y, m))
        y, m = _month_after(y, m, -1)
    if date(*months[-1], 1) < first:
        return {}
    means = {}
    for y, m in months:
        ny, nm = _month_after(y, m)
        n_days = (date(ny, nm, 1) - date(y, m, 1)).days
        total = sum(daily.get(date(y, m, 1) + timedelta(days=i), 0) for i in range(n_days))
        means[m] = total / n_days
    base = statistics.fmean(means.values())
    if base <= 0:
        return {}
    return {m: v / base for m, v in means.items()}


def _detect_sales(key, rows, as_of, use_season) -> tuple[list[Component], _SalesState | None]:
    category = key.split("|")[0]
    kind = "credit_day" if category == "cash_in" else "registry"
    daily: dict[date, int] = defaultdict(int)
    credit_days: set[date] = set()
    for d, amount, tx in rows:
        s = d if kind == "credit_day" else _sale_date(tx, d)
        daily[s] += amount
        credit_days.add(d)
    recent_days = [d for d in credit_days if d > as_of - timedelta(days=90)]
    need = 12 if kind == "credit_day" else 20
    if len(recent_days) < need or (as_of - max(credit_days)).days > 14:
        return [], None
    last = max(daily)
    window = [last - timedelta(days=i) for i in range(55, -1, -1)]
    if kind == "credit_day":
        window = [d for d in window if is_working_day(d)]
    by_wd: dict[int, list[int]] = defaultdict(list)
    for d in window:
        by_wd[d.weekday()].append(daily.get(d, 0))
    level = {w: statistics.fmean(v) for w, v in by_wd.items()}
    presence = {w: sum(x > 0 for x in v) / len(v) for w, v in by_wd.items()}
    season = _season_index(daily, last) if use_season and kind == "registry" else {}
    recent_season = statistics.fmean(season.get(d.month, 1.0) for d in window) if season else 1.0
    state = _SalesState(kind, last, level, presence, season, recent_season)
    total_presence = statistics.fmean(presence.values()) if presence else 0.0
    comp = Component(
        component_id=f"{category}:in",
        mechanism=category,
        category=category,
        direction="in",
        group_key=key,
        counterparty=_top_counterparty(rows),
        confidence=round(max(min(total_presence, 0.99), 0.05), 3),
        params=dict(
            kind=kind,
            last_sale=last.isoformat(),
            level_minor={str(w): int(v) for w, v in sorted(level.items())},
            presence={str(w): round(v, 3) for w, v in sorted(presence.items())},
            season_on=bool(season),
            season={str(m): round(v, 4) for m, v in sorted(season.items())},
            lag="next working day after the registry date (T+1)"
            if kind == "registry"
            else "same day",
        ),
    )
    return [comp], state


def _forecast_sale(state: _SalesState, s: date) -> float:
    base = state.level.get(s.weekday(), 0.0)
    if state.season and state.recent_season > 0:
        factor = state.season.get(s.month, 1.0) / state.recent_season
        base *= min(max(factor, 0.5), 2.0)
    return base


# --------------------------------------------------------------------------- expected events


def _component_events(comp: Component, as_of: date, horizon: int, state) -> list[ExpectedEvent]:
    end = as_of + timedelta(days=horizon)

    def make(d, amount, window=None, mechanism=None) -> ExpectedEvent:
        return ExpectedEvent(
            date=d,
            amount_minor=int(amount),
            direction=comp.direction,
            category=comp.category,
            mechanism=mechanism or comp.mechanism,
            component_id=comp.component_id,
            group_key=comp.group_key,
            counterparty=comp.counterparty,
            confidence=comp.confidence,
            window=window,
        )

    p = comp.params
    months = _months_covering(as_of - timedelta(days=31), end)
    out: list[ExpectedEvent] = []
    if comp.mechanism == "salary":
        for y, m in months:
            d = nominal_date(y, m, p["day"], p["rule"])
            if as_of < d <= end:
                out.append(make(d, p["amount_minor"]))
    elif (
        comp.mechanism == "enp"
        and hasattr(state, "forecast")
        and (planned := state.forecast(as_of, end)) is not None
    ):
        # X7 hook: a regime-aware tax calendar is the single owner of the ENP events
        for d, amount, window in planned:
            out.append(make(d, amount, window, mechanism="tax_calendar"))
    elif comp.mechanism == "enp":
        cal = DefaultTaxCalendar() if p["calendar"] == "DefaultTaxCalendar" else None
        adv = set(p["advance_months"])
        for y, m in months:
            d = _enp_due(p, y, m, cal)
            if not (as_of < d <= end):
                continue
            amount = p["advance_minor"] if m in adv else p["base_minor"]
            prev = _month_after(y, m, -1)
            month_end = date(*_month_after(*prev), 1) - timedelta(days=1)
            fot = p["salary_by_month"].get(f"{prev[0]}-{prev[1]:02d}")
            if p["fot_ratio"] and m not in adv and fot and as_of >= month_end:
                amount = p["fot_ratio"] * fot
            out.append(make(d, amount))
    elif comp.mechanism in ("fixed_monthly", "loan"):
        obs = [date.fromisoformat(x) for x in p["obs_days"]]
        for y, m in months:
            if p["date_model"] == "fixed_day":
                d, window = nominal_date(y, m, p["day"], p["rule"]), None
            else:
                day = p["day"]
                if (y, m) == (as_of.year, as_of.month):
                    later = [x for x in p["doms"] if x > as_of.day]
                    if not later:
                        continue
                    day = int(statistics.median(later))
                d = nominal_date(y, m, day, p["rule"])
                window = (
                    nominal_date(y, m, p["p10"], p["rule"]),
                    nominal_date(y, m, p["p90"], p["rule"]),
                )
            if not (as_of < d <= end):
                continue
            if any(o.year == d.year and o.month == d.month and o <= as_of for o in obs):
                continue  # this month's payment is already made
            out.append(make(d, p["amount_minor"], window))
    elif comp.mechanism in ("acquiring", "sbp") and state is not None:
        out.extend(_registry_events(comp, state, as_of, end, make))
    elif comp.mechanism == "cash_in" and state is not None:
        d = as_of
        while d < end:
            d += timedelta(days=1)
            amount = state.level.get(d.weekday(), 0.0)
            if is_working_day(d) and amount > 0 and state.presence.get(d.weekday(), 0) >= 0.25:
                out.append(make(d, amount))
    return [e for e in out if e.amount_minor > 0]


def _enp_due(p, year, month, cal) -> date:
    if cal is not None:
        return cal.due_date(year, month)
    return nominal_date(year, month, 28, "next")


def _registry_events(comp, state, as_of, end, make) -> list[ExpectedEvent]:
    out = []
    d = as_of
    while d < end:
        d += timedelta(days=1)
        if not is_working_day(d):
            continue
        # Sales dates credited on d: from the previous working day up to the day before d.
        first = prev_working_day(d - timedelta(days=1))
        total = 0.0
        s = first
        while s < d:
            if s > state.last_sale:
                total += _forecast_sale(state, s)
            s += timedelta(days=1)
        if total > 0:
            out.append(make(d, total))
    return out


__all__ = [
    "ExpectedEvent",
    "Component",
    "MechanismModel",
    "TaxCalendar",
    "DefaultTaxCalendar",
    "detect_mechanisms",
    "group_key",
    "fit_nominal_days",
    "next_working_day",
    "working_days_between",
]
