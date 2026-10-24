from datetime import date, timedelta

from cashflow_ml import calendar_ru as cal
from cashflow_ml.mechanisms import (
    DefaultTaxCalendar,
    detect_mechanisms,
    fit_nominal_days,
    group_key,
)

_ID = iter(range(10**9))


def tx(day, amount, category, direction="out", inn="111", name="Контрагент", purpose=""):
    return dict(
        operation_id=f"T{next(_ID)}",
        operation_date=day.isoformat(),
        amount_minor=amount,
        direction=direction,
        counterparty_inn=inn,
        counterparty_name=name,
        payment_purpose=purpose,
        category=category,
    )


def months(start, n):
    y, m = start
    for _ in range(n):
        yield y, m
        y, m = (y + (m == 12), m % 12 + 1)


def history_monthly(day, amount, category, rule, start=(2025, 1), n=14, **kw):
    return [
        tx(cal.nominal_date(y, m, day, rule), amount, category, **kw) for y, m in months(start, n)
    ]


AS_OF = date(2026, 2, 1)


def test_fit_nominal_days_recovers_day_and_shift():
    obs = [cal.nominal_date(2025, m, 10, "prev") for m in range(1, 9)]
    ((k, hits),) = fit_nominal_days(obs, "prev", 1, 3)
    assert k == 10 and len(hits) == 8
    assert fit_nominal_days(obs[:2], "prev", 1, 3) == []


def test_salary_two_paydays_with_prev_rule():
    txs = history_monthly(10, 3_000_000, "salary", "prev", name="Банк", inn="b")
    txs += history_monthly(25, 2_000_000, "salary", "prev", name="Банк", inn="b")
    model = detect_mechanisms(txs, AS_OF)
    salary = [e for e in model.events(30) if e.mechanism == "salary"]
    assert [(e.date, e.amount_minor) for e in salary] == [
        (date(2026, 2, 10), 3_000_000),
        (date(2026, 2, 25), 2_000_000),
    ]
    assert {c.component_id for c in model.components} == {"salary:out:d10", "salary:out:d25"}
    # 2026-03-01 horizon: 25 Feb Wednesday; 10 Mar -> Tuesday (9 Mar is a holiday)
    later = detect_mechanisms(txs, date(2026, 2, 28)).events(30)
    assert date(2026, 3, 10) in [e.date for e in later]


def test_salary_amount_is_median_of_last_three():
    txs = history_monthly(10, 3_000_000, "salary", "prev", n=13)  # last payment 10 Jan 2026
    amounts = [3_000_000, 3_000_000, 3_000_000, 9_000_000, 3_300_000, 3_400_000]
    for t, a in zip(txs[-6:], amounts, strict=True):
        t["amount_minor"] = a
    event = detect_mechanisms(txs, AS_OF).events(30)[0]
    assert event.amount_minor == 3_400_000  # median of 9.0M, 3.3M, 3.4M


def test_enp_next_working_day_and_advance_month():
    txs = []
    for y, m in months((2025, 1), 14):
        amount = 9_000_000 if m in (4, 7, 10) else 4_000_000
        txs.append(tx(DefaultTaxCalendar().due_date(y, m), amount, "tax", name="ФНС", inn="fns"))
    model = detect_mechanisms(txs, date(2026, 3, 1))
    events = model.events(60)
    assert [(e.date, e.amount_minor) for e in events] == [
        (date(2026, 3, 2), 4_000_000),  # 28 Feb is Saturday
        (date(2026, 3, 30), 4_000_000),  # 28 Mar is Saturday
        (date(2026, 4, 28), 9_000_000),
    ]
    assert all(e.mechanism == "enp" and e.category == "tax" for e in events)


def test_fixed_day_rent_and_bank_fee():
    txs = history_monthly(1, 5_000_000, "rent", "next", inn="rent", name="Арендодатель")
    txs += history_monthly(5, 99_000, "bank_fee", "next", inn="bank", name="Банк")
    model = detect_mechanisms(txs, AS_OF)
    got = {(e.category, e.date, e.amount_minor) for e in model.events(25)}
    assert got == {
        ("rent", date(2026, 2, 2), 5_000_000),  # 1 Feb 2026 is Sunday
        ("bank_fee", date(2026, 2, 5), 99_000),
    }
    assert all(0 < e.confidence <= 0.99 for e in model.events(25))


def test_jittery_fixed_component_skips_month_already_paid():
    days = [3, 17, 9, 24, 12, 20, 6, 15, 22, 8, 18, 11, 25, 13]
    txs = [
        tx(date(y, m, d), 400_000, "software", inn="sw")
        for (y, m), d in zip(months((2025, 1), 14), days, strict=True)
    ]
    # paid on 2 Feb already: no second February event
    txs.append(tx(date(2026, 2, 2), 400_000, "software", inn="sw"))
    model = detect_mechanisms(txs, date(2026, 2, 3))
    assert all(e.date.month != 2 for e in model.events(30))
    [event] = detect_mechanisms(txs[:-1], date(2026, 2, 1)).events(30)
    assert event.window is not None and event.window[0] <= event.date <= event.window[1]


def test_loan_annuity_by_contract_and_unstable_amount_rejected():
    purpose = "Погашение кредита по договору № КД-1404 от 16.12.2021: основной долг 1, проценты 2"
    txs = history_monthly(22, 10_412_805, "debt_service", "next", purpose=purpose, inn="bank")
    model = detect_mechanisms(txs, AS_OF)
    [event] = model.events(30)
    assert (event.mechanism, event.date, event.amount_minor) == (
        "loan",
        date(2026, 2, 24),
        10_412_805,
    )  # 22nd is Sunday, 23rd a holiday
    assert group_key(txs[0]) == "debt_service|out|КД-1404"
    varying = history_monthly(22, 1_000_000, "debt_service", "next", inn="x")
    for i, t in enumerate(varying):
        t["amount_minor"] = 1_000_000 + 600_000 * (i % 2)
    assert not detect_mechanisms(varying, AS_OF).components


def _sales(category, base=1_000_000, start=date(2025, 6, 1), end=date(2026, 1, 31)):
    rows = []
    s = start
    while s <= end:
        credit = cal.next_working_day(s + timedelta(days=1))
        if credit <= end + timedelta(days=1):
            weekday_factor = 1 + 0.5 * (s.weekday() >= 4)
            purpose = f"Зачисление средств. Дата реестра {s.strftime('%d.%m.%Y')}. Сумма операций 1"
            rows.append(
                tx(
                    credit,
                    int(base * weekday_factor),
                    category,
                    "in",
                    inn="acq",
                    name="Банк",
                    purpose=purpose,
                )
            )
        s += timedelta(days=1)
    return rows


def test_acquiring_t_plus_one_working_day_and_weekend_roll():
    txs = _sales("acquiring")
    as_of = date(2026, 2, 1)  # Sunday; last credited registry day is Thursday... Friday is open
    history = [t for t in txs if t["operation_date"] <= as_of.isoformat()]
    model = detect_mechanisms(history, as_of)
    events = model.events(10)
    dates = [e.date for e in events]
    assert dates[0] == date(2026, 2, 2)  # Monday collects Fri + Sat + Sun registries
    assert all(cal.is_working_day(d) for d in dates)
    monday = events[0].amount_minor
    assert monday == 1_500_000 * 3  # Fri, Sat, Sun sales at the 1.5 weekday factor
    assert events[1].amount_minor == 1_000_000  # Tuesday: Monday sales only


def test_no_future_leak_and_ownership():
    txs = history_monthly(10, 3_000_000, "salary", "prev", name="Банк", inn="b")
    future = [t for t in txs if t["operation_date"] > "2026-01-01"]
    model_full = detect_mechanisms(txs, date(2026, 1, 1))
    model_hist = detect_mechanisms([t for t in txs if t not in future], date(2026, 1, 1))
    assert model_full.to_dict() == model_hist.to_dict()
    owned = model_full.owned_operation_ids(txs)
    assert owned and all(
        t["operation_id"] not in owned or t["operation_date"] <= "2026-01-01" for t in txs
    )
    assert all(model_full.owns(t) for t in txs)
    assert not model_full.owns(tx(date(2026, 1, 5), 10, "supplier"))
    assert model_full.assign(txs[0]) == "salary:out:d10"
    other = tx(date(2025, 12, 5), 100, "salary", name="Сотрудник 01", inn="")
    assert model_full.owns(other)


def test_planned_event_conversion():
    txs = history_monthly(10, 3_000_000, "salary", "prev", name="Банк", inn="b")
    model = detect_mechanisms(txs, AS_OF)
    [pe, *_] = model.planned_events("C001", 30)
    assert pe.amount_minor == 3_000_000 and pe.direction == "out"
    assert pe.source == "regular" and pe.status == "assumption" and pe.known_at == AS_OF
    assert pe.category == "salary" and pe.expected_date == pe.due_date == date(2026, 2, 10)


def test_stale_component_is_dropped():
    txs = history_monthly(1, 5_000_000, "rent", "next", n=8, inn="rent")  # ends Aug 2025
    assert not detect_mechanisms(txs, date(2026, 2, 1)).components
