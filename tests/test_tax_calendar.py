from datetime import date, timedelta

from cashflow_ml.calendar_ru import nominal_date
from cashflow_ml.mechanisms import detect_mechanisms
from cashflow_ml.tax_calendar import (
    RegimeTaxCalendar,
    build_tax_calendar,
    charge_periods,
    infer_regime,
    naive_median_forecast,
    nominal_month,
    tax_events,
)

KOP = 100
BASE = 400_000 * KOP
DAILY = 100_000 * KOP
RATE = 0.03


def _tx(n, day, amount, category, direction, purpose=""):
    return dict(
        operation_id=f"T{n}",
        operation_date=day.isoformat(),
        amount_minor=amount,
        direction=direction,
        counterparty_inn="7707083893",
        counterparty_name="Казначейство России (ФНС России)",
        payment_purpose=purpose,
        category=category,
    )


def _quarter_days(year, q):
    first = date(year, 3 * q - 2, 1)
    last = nominal_date(year + (q == 4), 1 if q == 4 else 3 * q + 1, 1, "none") - timedelta(days=1)
    return (last - first).days + 1


def company(first=(2024, 10), last=(2026, 3), spikes=(4, 7, 10), annual=3, base=BASE):
    """Constant revenue 100k a day, salary on the 10th, ENP = base + 3% of the periods' revenue."""
    txs, n = [], 0
    start = date(*first, 1)
    end = nominal_date(last[0] + (last[1] == 12), last[1] % 12 + 1, 1, "none") - timedelta(days=1)
    d = start
    while d <= end:
        n += 1
        txs.append(_tx(n, d, DAILY, "customer_payment", "in", "Оплата по счёту"))
        if d.day == 10:
            n += 1
            txs.append(_tx(n, d, 1_000_000 * KOP, "salary", "out", "Зарплата"))
        d += timedelta(days=1)
    y, m = first
    while (y, m) <= last:
        amount = base
        if m in spikes:
            q = {4: 1, 7: 2, 10: 3}[m]
            amount += int(RATE * _quarter_days(y, q) * DAILY)
        if m == annual:
            amount += int(RATE * _quarter_days(y - 1, 4) * DAILY)
        n += 1
        txs.append(
            _tx(n, nominal_date(y, m, 28, "next"), amount, "tax", "out", "Единый налоговый платеж")
        )
        y, m = (y + (m == 12), m % 12 + 1)
    return txs


def test_nominal_month_carries_the_28th_over_the_weekend():
    # 28 Feb 2026 is a Saturday: the payment moves to Monday 2 March but belongs to February
    assert nominal_month(date(2026, 3, 2)) == (2026, 2)
    assert nominal_month(date(2026, 3, 30)) == (2026, 3)
    assert nominal_month(date(2026, 4, 28)) == (2026, 4)
    assert nominal_month(date(2026, 3, 17)) == (2026, 3)


def test_charge_periods_by_regime_and_legal_form():
    assert charge_periods("usn_6", "ООО", 2026, 3) == [(date(2025, 10, 1), date(2025, 12, 31))]
    assert charge_periods("usn_6", "ИП", 2026, 3) == []
    april_ip = charge_periods("usn_6", "ИП", 2026, 4)
    assert (date(2026, 1, 1), date(2026, 3, 31)) in april_ip and len(april_ip) == 2
    assert charge_periods("usn_15", "ООО", 2026, 7) == [(date(2026, 4, 1), date(2026, 6, 30))]
    assert charge_periods("esxn", "ООО", 2026, 7) == [(date(2026, 1, 1), date(2026, 6, 30))]
    assert charge_periods("esxn", "ООО", 2026, 10) == []
    assert charge_periods("osno", "ООО", 2026, 4) == []
    assert charge_periods("unknown", "ООО", 2026, 4) == []


def test_usn_regime_is_inferred_from_quarterly_charges():
    txs = company()
    regime = infer_regime(txs, date(2026, 3, 15), "ООО")
    assert regime.regime == "usn_6"
    assert regime.confidence > 0.8
    assert regime.evidence["quarter_months_with_charge"] == [4, 7, 10]


def test_esxn_regime_has_a_half_year_charge_only():
    txs = company(spikes=(7,), annual=3)
    regime = infer_regime(txs, date(2026, 3, 15), "ООО")
    assert regime.regime == "esxn"


def test_payroll_level_without_charges_is_osno():
    txs = company(spikes=(), annual=0, base=900_000 * KOP)
    regime = infer_regime(txs, date(2026, 3, 15), "ООО")
    assert regime.regime == "osno"


def test_short_history_is_unknown_and_defers_to_x2():
    txs = company(first=(2025, 11), last=(2026, 3))
    cal = build_tax_calendar(txs, date(2026, 3, 15), "ООО")
    assert cal.regime.regime == "unknown"
    assert cal.regime.confidence == 0.0
    assert not cal.active
    assert cal.forecast(date(2026, 3, 15), date(2026, 4, 14)) is None


def test_quarter_advance_is_rate_times_revenue_of_the_quarter():
    txs = company()
    as_of = date(2026, 4, 10)
    cal = build_tax_calendar([t for t in txs if t["operation_date"] <= as_of.isoformat()],
                             as_of, "ООО")  # fmt: skip
    events = cal.forecast(as_of, as_of + timedelta(days=30))
    assert [e[0] for e in events] == [date(2026, 4, 28)]
    expected = BASE + RATE * 90 * DAILY
    assert abs(events[0][1] - expected) / expected < 0.02


def test_ordinary_month_is_the_monthly_part():
    txs = company(last=(2026, 4))
    as_of = date(2026, 5, 10)
    cal = build_tax_calendar([t for t in txs if t["operation_date"] <= as_of.isoformat()],
                             as_of, "ООО")  # fmt: skip
    (event,) = cal.forecast(as_of, as_of + timedelta(days=30))
    assert event[0] == date(2026, 5, 28)
    assert abs(event[1] - BASE) / BASE < 0.02


def test_due_date_moves_to_next_working_day():
    cal = RegimeTaxCalendar([], date(2026, 3, 1))
    assert cal.due_date(2026, 2) == date(2026, 3, 2)  # 28 Feb 2026 is a Saturday
    assert cal.due_date(2026, 4) == date(2026, 4, 28)
    assert cal.is_advance_month(4) is True  # inactive calendar: X2 default advance months
    assert cal.is_advance_month(5) is False


def test_no_leak_from_future_operations():
    txs = company(last=(2026, 8))
    as_of = date(2026, 4, 10)
    cut = [t for t in txs if t["operation_date"] <= as_of.isoformat()]
    a = build_tax_calendar(cut, as_of, "ООО")
    b = build_tax_calendar(txs, as_of, "ООО")
    assert a.regime.to_dict() == b.regime.to_dict()
    end = as_of + timedelta(days=30)
    assert a.forecast(as_of, end) == b.forecast(as_of, end)
    assert infer_regime(txs, as_of, "ООО") == a.regime


def test_single_owner_of_enp_events():
    txs = company()
    as_of = date(2026, 4, 10)
    cut = [t for t in txs if t["operation_date"] <= as_of.isoformat()]
    events, cal = tax_events(cut, as_of, "ООО")
    assert cal.active
    assert [e.mechanism for e in events] == ["tax_calendar"]
    assert events[0].category == "tax" and events[0].direction == "out"
    model = detect_mechanisms(cut, as_of, tax_calendar=cal, use_season=False)
    enp_like = [e for e in model.events(30) if e.category == "tax"]
    assert len(enp_like) == 1  # the calendar replaces the X2 ENP event, never duplicates it
    planned = events[0].to_planned_event("A1", as_of)
    assert planned.amount_minor == events[0].amount_minor
    assert "tax_calendar" in planned.label


def test_unknown_regime_keeps_x2_events():
    txs = company(first=(2025, 9), last=(2026, 3))
    as_of = date(2026, 4, 10)
    cut = [t for t in txs if t["operation_date"] <= as_of.isoformat()]
    events, cal = tax_events(cut, as_of, "ООО")
    assert not cal.active
    assert all(e.mechanism == "enp" for e in events)
    default = detect_mechanisms(cut, as_of, use_season=False).events(30)
    assert [(e.date, e.amount_minor) for e in events] == [
        (e.date, e.amount_minor) for e in default if e.category == "tax"
    ]


def test_month_already_paid_early_gives_no_second_event():
    txs = company(last=(2026, 4))
    as_of = date(2026, 5, 10)
    cut = [t for t in txs if t["operation_date"] <= as_of.isoformat()]
    cut.append(_tx(99999, date(2026, 5, 8), BASE, "tax", "out", "Единый налоговый платеж"))
    cal = build_tax_calendar(cut, as_of, "ООО")
    assert cal.forecast(as_of, as_of + timedelta(days=30)) == []


def test_enforcement_collections_are_not_enp():
    txs = company(last=(2026, 4))
    as_of = date(2026, 5, 10)
    cut = [t for t in txs if t["operation_date"] <= as_of.isoformat()]
    base = build_tax_calendar(cut, as_of, "ООО")
    cut.append(
        _tx(
            99999,
            date(2026, 4, 20),
            50_000_000 * KOP,
            "tax",
            "out",
            "Списание по поручению налогового органа № 1. Взыскание задолженности",
        )
    )
    other = build_tax_calendar(cut, as_of, "ООО")
    assert other.params["base_minor"] == base.params["base_minor"]
    assert other.regime.regime == base.regime.regime


def test_naive_baseline_is_median_of_last_three():
    txs = company(last=(2026, 4))
    as_of = date(2026, 5, 10)
    cut = [t for t in txs if t["operation_date"] <= as_of.isoformat()]
    (d, amount), *_ = naive_median_forecast(cut, as_of, 30)
    assert d == date(2026, 5, 28)
    assert amount > 0
