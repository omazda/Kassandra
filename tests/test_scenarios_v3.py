from datetime import date, timedelta
from decimal import Decimal

import pytest
from conftest import OWNER
from pydantic import ValidationError

from cashflow_domain.engine import fixed_cost_share, scenario
from cashflow_domain.explain import explain_gap, fmt_money
from cashflow_domain.schemas import EventCreate, ForecastRequest, PlannedEvent, ScenarioSpec

AS_OF = date(2026, 4, 30)


def base_run(svc, **kw):
    return svc.forecast(ForecastRequest(account_id="A", as_of=AS_OF, model="mean", **kw))


def spec(base, **kw):
    return ScenarioSpec(base_run_id=base.run_id, **kw)


def event(**kw):
    values = dict(
        id="e1",
        account_id="A",
        component_id="manual",
        category="customer_payment",
        amount_minor=100000,
        direction="in",
        due_date=date(2026, 5, 10),
        expected_date=date(2026, 5, 10),
        known_at=AS_OF,
        source="manual",
        status="confirmed",
        counterparty_inn="7700000001",
        counterparty_name="Клиент X",
    )
    return PlannedEvent(**{**values, **kw})


def test_planned_event_new_fields_optional():
    e = event(counterparty_inn=None, counterparty_name=None)
    assert e.document_ref is None
    # an older stored payload without the new keys still loads
    raw = e.model_dump(mode="json")
    for k in ("counterparty_inn", "counterparty_name", "document_ref"):
        raw.pop(k)
    assert PlannedEvent.model_validate(raw).counterparty_inn is None


def test_fixed_cost_increase_events_and_residual(svc):
    svc.add_event(
        EventCreate(
            event=event(
                id="rent-m", category="rent", direction="out", expected_date=date(2026, 5, 15)
            )
        ),
        OWNER,
    )
    svc.add_event(
        EventCreate(
            event=event(
                id="sup", category="supplier", direction="out", expected_date=date(2026, 5, 15)
            )
        ),
        OWNER,
    )
    base = base_run(svc)
    res = svc.evaluate_scenario(spec(base, kind="fixed_cost_increase", fixed_cost_pct=10))
    by_id = {e.id: e for e in res.events}
    assert by_id["rent-m"].amount_minor == 110000
    assert by_id["sup"].amount_minor == 100000
    # regular rent events also grow
    for e in base.events:
        if e.category == "rent" and e.source == "regular":
            assert next(x for x in res.events if x.id == e.id).amount_minor == (
                e.amount_minor * 11 // 10
            )
    share = fixed_cost_share(svc.store.transactions("A", AS_OF), AS_OF)
    assert share == 1  # fixture: the only outflow is rent
    assert res.delta_end_minor < 0
    assert any("доле" in a and "фиксированных" in a for a in res.assumptions)
    assert svc.get_forecast(base.run_id) == base


def test_fixed_cost_residual_scaled_by_history_share(svc):
    base = base_run(svc)
    points = [p.model_copy(update={"residual_out_minor": 1000}) for p in base.points]
    flat = base.model_copy(update={"points": points})
    res = scenario(
        flat,
        spec(flat, kind="fixed_cost_increase", fixed_cost_pct=10, start_date=date(2026, 5, 11)),
        "t",
        {"fixed_share": Decimal("0.4")},
    )
    # +10% of fixed costs, fixed costs are 40% of outflow: residual outflow +4% from the start day
    for p in res.points:
        assert p.residual_out_minor == (1040 if p.date >= date(2026, 5, 11) else 1000)
    with pytest.raises(ValueError, match="доля"):
        scenario(flat, spec(flat, kind="fixed_cost_increase"), "t")


def test_fixed_cost_start_date_and_categories(svc):
    svc.add_event(
        EventCreate(
            event=event(
                id="rent-m", category="rent", direction="out", expected_date=date(2026, 5, 15)
            )
        ),
        OWNER,
    )
    base = base_run(svc)
    late = svc.evaluate_scenario(
        spec(base, kind="fixed_cost_increase", fixed_cost_pct=50, start_date=date(2026, 5, 20))
    )
    assert next(e for e in late.events if e.id == "rent-m").amount_minor == 100000
    assert all(
        a.residual_out_minor == b.residual_out_minor
        for a, b in zip(late.points, base.points, strict=True)
        if a.date < date(2026, 5, 20)
    )
    other = svc.evaluate_scenario(
        spec(base, kind="fixed_cost_increase", fixed_cost_pct=50, categories=["utilities"])
    )
    assert next(e for e in other.events if e.id == "rent-m").amount_minor == 100000


def test_fixed_share_uses_only_history_up_to_as_of():
    txs = [
        dict(direction="out", operation_date="2026-04-10", amount_minor=100, category="rent"),
        dict(direction="out", operation_date="2026-04-11", amount_minor=300, category="supplier"),
        dict(direction="out", operation_date="2026-05-02", amount_minor=9000, category="supplier"),
        dict(
            direction="out", operation_date="2026-04-12", amount_minor=500, category="own_transfer"
        ),
        dict(direction="in", operation_date="2026-04-12", amount_minor=500, category="rent"),
    ]
    assert fixed_cost_share(txs, AS_OF) * 4 == 1


def test_client_delay_moves_all_client_events(svc):
    for i, day in enumerate([5, 12]):
        svc.add_event(EventCreate(event=event(id=f"c{i}", expected_date=date(2026, 5, day))), OWNER)
    svc.add_event(
        EventCreate(
            event=event(
                id="other",
                counterparty_inn="7700000002",
                counterparty_name="Другой",
                expected_date=date(2026, 5, 6),
            )
        ),
        OWNER,
    )
    base = base_run(svc)
    res = svc.evaluate_scenario(
        spec(base, kind="client_delay", counterparty="7700000001", delay_days=60)
    )
    by_id = {e.id: e for e in res.events}
    assert by_id["c0"].expected_date == date(2026, 7, 4)
    assert by_id["c1"].expected_date == date(2026, 7, 11)
    assert by_id["other"].expected_date == date(2026, 5, 6)
    assert res.delta_end_minor == -200000
    by_name = svc.evaluate_scenario(
        spec(base, kind="client_delay", counterparty="клиент x", delay_days=3)
    )
    assert next(e for e in by_name.events if e.id == "c0").expected_date == date(2026, 5, 8)
    with pytest.raises(ValueError, match="нет будущих поступлений"):
        svc.evaluate_scenario(spec(base, kind="client_delay", counterparty="0000", delay_days=5))


def test_client_delay_validation(svc):
    base = base_run(svc)
    with pytest.raises(ValidationError):
        spec(base, kind="client_delay", counterparty="x", delay_days=61)
    with pytest.raises(ValidationError):
        spec(base, kind="client_delay", delay_days=5)


def test_regular_events_carry_counterparty(svc):
    base = base_run(svc)
    regular = [e for e in base.events if e.source == "regular"]
    assert regular and all(e.counterparty_inn for e in regular)
    friday = next(e for e in regular if e.counterparty_inn == "0011111111")
    res = svc.evaluate_scenario(
        spec(base, kind="client_delay", counterparty="0011111111", delay_days=30)
    )
    moved = next(e for e in res.events if e.id == friday.id)
    assert moved.expected_date == friday.expected_date + timedelta(days=30)


def test_top_clients_90_days_only_revenue(svc):
    rows = svc.top_clients("A", AS_OF)
    assert [r["counterparty_inn"] for r in rows][:2] == ["0012345678", "0011111111"]
    assert rows[0]["amount_minor"] > rows[1]["amount_minor"]
    assert abs(sum(r["share"] for r in rows) - 1) < 1e-3
    # window is exactly 90 days: day 90 days before as_of is excluded
    since = str(AS_OF - timedelta(days=90))
    inside = [
        t
        for t in svc.store.transactions("A", AS_OF)
        if t["counterparty_inn"] == "0012345678" and t["operation_date"] > since
    ]
    assert rows[0]["amount_minor"] == sum(t["amount_minor"] for t in inside)
    base = base_run(svc)
    with_up = svc.top_clients("A", AS_OF, run_id=base.run_id)
    assert all("upcoming_events" in r for r in with_up)
    assert svc.top_clients("A", AS_OF, limit=1)[0]["counterparty_inn"] == "0012345678"


def test_combined_scenario(svc):
    svc.add_event(EventCreate(event=event(id="c0", expected_date=date(2026, 5, 5))), OWNER)
    base = base_run(svc)
    combo = svc.evaluate_scenario(
        spec(
            base,
            kind="combined",
            parts=[
                {"kind": "revenue_drop", "revenue_drop_pct": 10},
                {"kind": "fixed_cost_increase", "fixed_cost_pct": 10},
                {"kind": "client_delay", "counterparty": "7700000001", "delay_days": 14},
                {"kind": "extra_expense", "amount_minor": 5000, "expense_date": "2026-05-12"},
            ],
        )
    )
    singles = [
        svc.evaluate_scenario(spec(base, kind="revenue_drop", revenue_drop_pct=10)),
        svc.evaluate_scenario(spec(base, kind="fixed_cost_increase", fixed_cost_pct=10)),
    ]
    assert combo.delta_end_minor < min(s.delta_end_minor for s in singles)
    assert len(combo.assumptions) >= 5
    assert any(e.category == "operating_expense" and e.source == "manual" for e in combo.events)
    again = svc.get_scenario(combo.scenario_id)
    assert again == combo


def test_combined_validation(svc):
    base = base_run(svc)
    with pytest.raises(ValidationError):
        spec(base, kind="combined", parts=[{"kind": "revenue_drop"}])
    with pytest.raises(ValidationError):
        spec(base, kind="revenue_drop", parts=[{"kind": "revenue_drop"}] * 2)
    with pytest.raises(ValidationError):
        spec(
            base,
            kind="combined",
            parts=[{"kind": "revenue_drop"}, {"kind": "revenue_drop", "revenue_drop_pct": 5}],
        )


def test_fmt_money():
    assert fmt_money(34_000_000) == "340 тыс. ₽"
    assert fmt_money(34_000_001, up=True) == "341 тыс. ₽"
    assert fmt_money(120_000_000) == "1,20 млн ₽"
    assert fmt_money(99_990_000) == "1,00 млн ₽"
    assert fmt_money(530_000) == "5,3 тыс. ₽"
    assert fmt_money(85_000) == "850 ₽"


def gap_case(svc):
    svc.add_event(
        EventCreate(
            event=event(
                id="enp",
                category="tax",
                direction="out",
                amount_minor=18_000_000,
                expected_date=date(2026, 5, 20),
                label="ЕНП",
            )
        ),
        OWNER,
    )
    svc.add_event(
        EventCreate(
            event=event(
                id="sup",
                category="supplier",
                direction="out",
                amount_minor=2_000_000,
                expected_date=date(2026, 5, 18),
                label="Поставщик Альфа",
            )
        ),
        OWNER,
    )
    svc.add_event(
        EventCreate(
            event=event(
                id="late",
                amount_minor=7_000_000,
                expected_date=date(2026, 6, 25),
                counterparty_name="ООО Ромашка",
            )
        ),
        OWNER,
    )
    opening = svc.store.daily("A")[211]["balance_minor"]
    return base_run(svc, reserve_minor=opening - 12_000_000)


def test_explanation_template_numbers_from_calculation(svc):
    base = gap_case(svc)
    assert base.risk.first_gap_date is not None
    ex = svc.explain(run_id=base.run_id)
    assert ex.has_gap and ex.gap_date == base.risk.first_gap_date
    assert ex.shortfall_minor == base.risk.liquidity_need_minor
    assert ex.text.startswith(f"Когда: {ex.gap_date.strftime('%d.%m')}")
    assert "Не хватит: " in ex.text and "Почему: " in ex.text and "Что сделать: " in ex.text
    assert ex.shortfall in ex.text
    assert any("Налоги" in w and "180 тыс. ₽" in w for w in ex.why)
    codes = [a.code for a in ex.actions]
    assert codes[:2] == ["postpone_supplier", "speed_up_client"] or "credit_line" in codes
    assert "credit_line" in codes and "reserve" in codes
    sup = next(a for a in ex.actions if a.code == "postpone_supplier")
    assert "Поставщик Альфа" in sup.text and sup.amount_minor == 2_000_000
    fast = next(a for a in ex.actions if a.code == "speed_up_client")
    assert "ООО Ромашка" in fast.text
    credit = next(a for a in ex.actions if a.code == "credit_line")
    assert credit.amount_minor == ex.shortfall_minor


def test_explanation_no_gap_and_scenario(svc):
    base = base_run(svc)
    ex = explain_gap(base, base.points, base.events, base.risk)
    assert not ex.has_gap and ex.text.startswith("Разрыва нет")
    gapped = gap_case(svc)
    res = svc.evaluate_scenario(spec(gapped, kind="revenue_drop", revenue_drop_pct=30))
    sc = svc.explain(scenario_id=res.scenario_id)
    assert sc.has_gap and any(w.startswith("Сценарий:") for w in sc.why)
    assert sc.shortfall_minor == res.risk.liquidity_need_minor
