from datetime import date, timedelta

import pytest
from conftest import OWNER

from cashflow_domain.engine import assemble, deterministic_risk
from cashflow_domain.schemas import (
    EventCreate,
    ForecastRequest,
    PaymentLink,
    PlannedEvent,
    ScenarioSpec,
)


def make_event(**updates):
    values = dict(
        id="invoice",
        account_id="A",
        component_id="manual",
        category="revenue",
        amount_minor=100000,
        direction="in",
        due_date=date(2026, 5, 10),
        expected_date=date(2026, 5, 10),
        known_at=date(2026, 4, 30),
        source="manual",
        status="confirmed",
    )
    return PlannedEvent(**{**values, **updates})


def test_replace_not_add_and_idempotent_event(svc):
    req = ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="mean")
    before = svc.forecast(req)
    target = next(e for e in before.events if e.category == "rent")
    replacement = make_event(
        id="rent-confirmed",
        component_id=target.component_id,
        category="rent",
        direction="out",
        amount_minor=target.amount_minor,
        due_date=target.due_date,
        expected_date=target.expected_date,
        replacement_id=target.id,
        decision="replace",
    )
    create = EventCreate(event=replacement, base_run_id=before.run_id)
    svc.add_event(create, OWNER)
    svc.add_event(create, OWNER)
    after = svc.forecast(req)
    assert before.points == after.points
    assert target.id not in {e.id for e in after.events}
    assert sum(e.component_id == target.component_id for e in after.events) == 1
    assert svc.get_forecast(before.run_id) == before


def test_delay_removes_old_date_and_retains_beyond_horizon(svc):
    event = make_event()
    svc.add_event(EventCreate(event=event), OWNER)
    base = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    )
    result = svc.evaluate_scenario(
        ScenarioSpec(base_run_id=base.run_id, kind="delay", event_id=event.id, delay_days=40)
    )
    moved = next(e for e in result.events if e.id == event.id)
    assert moved.expected_date == event.expected_date + timedelta(days=40)
    assert result.delta_end_minor == -event.amount_minor
    assert svc.get_forecast(base.run_id) == base
    assert all(p.p10_minor is None for p in result.points)


def test_partial_payment_not_counted_in_balance_and_future_twice(svc):
    event = make_event(amount_minor=100000, expected_date=date(2026, 5, 20))
    svc.add_event(EventCreate(event=event), OWNER)
    # in-211 is booked on Apr 30: 50001 minor already included in opening balance.
    tx = next(
        t
        for t in svc.store.transactions("A")
        if t["operation_date"] == "2026-04-30" and t["direction"] == "in"
    )
    payment = PaymentLink(
        operation_id=tx["operation_id"], amount_minor=50001, known_at=date(2026, 4, 30)
    )
    svc.link_payment(event.id, payment, OWNER)
    assert svc.link_payment(event.id, payment, OWNER)["status"] == "already_linked"
    result = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    )
    saved = next(e for e in result.events if e.id == event.id)
    assert saved.remaining_minor == 49999 and saved.status == "partially_paid"
    assert result.opening_balance_minor == svc.store.daily("A")[211]["balance_minor"]
    with pytest.raises(ValueError):
        svc.link_payment(
            event.id,
            PaymentLink(
                operation_id=tx["operation_id"], amount_minor=50002, known_at=date(2026, 4, 30)
            ),
            OWNER,
        )


def test_known_at_and_payment_knowledge_are_respected(svc):
    event = make_event(known_at=date(2026, 5, 1))
    svc.add_event(EventCreate(event=event), OWNER)
    past = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    )
    assert event.id not in {e.id for e in past.events}


def test_revenue_drop_excludes_financing_and_transfers(svc):
    for key, category in [
        ("income", "revenue"),
        ("owner", "owner_funding"),
        ("transfer", "own_transfer"),
    ]:
        svc.add_event(EventCreate(event=make_event(id=key, category=category)), OWNER)
    base = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    )
    scenario = svc.evaluate_scenario(
        ScenarioSpec(base_run_id=base.run_id, kind="revenue_drop", revenue_drop_pct=100)
    )
    assert all(p.residual_revenue_minor == 0 for p in scenario.points)
    assert {e.id for e in scenario.events if e.id in {"owner", "transfer"}} == {"owner", "transfer"}
    assert next(e for e in scenario.events if e.id == "owner").amount_minor == 100000
    assert not any(e.id == "income" for e in scenario.events)


def test_current_gap_distinguished_from_new_gap():
    points = assemble(date(2026, 1, 1), -100, [[0, 0, 10]] * 30, [])
    current = deterministic_risk(-100, points)
    assert current.current_gap_minor == 100 and not current.new_risk
    points = assemble(date(2026, 1, 1), 100, [[0, 0, 10]] * 30, [])
    future = deterministic_risk(100, points)
    assert not future.current_gap_minor and future.new_risk
    assert future.first_gap_date == date(2026, 1, 12)
    credit = deterministic_risk(100, points, credit=1000)
    assert not credit.new_risk


def test_immutable_forecast_survives_restart_and_versions(svc):
    from cashflow_services.service import Service

    base = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    )
    svc.add_event(EventCreate(event=make_event()), OWNER)
    new = svc.forecast(ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural"))
    assert base.event_version + 1 == new.event_version
    assert new.run_id != base.run_id
    restarted = Service(svc.store.path, svc.artifacts)
    assert restarted.get_forecast(base.run_id) == base
    with restarted.store.connect() as db, pytest.raises(Exception, match="immutable"):
        db.execute("UPDATE forecasts SET payload='{}' WHERE run_id=?", (base.run_id,))
