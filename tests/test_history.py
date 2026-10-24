from datetime import date

from cashflow_domain.schemas import ForecastRequest


def test_category_knowledge_does_not_change_earlier_cut(client, svc, auth):
    before = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    )
    result = client.patch(
        "/api/v1/transactions/A/in-0/category",
        headers=auth,
        json={"category": "own_transfer", "known_at": "2026-05-01"},
    )
    assert result.status_code == 200
    after = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    )
    assert before.points == after.points
    assert svc.store.transactions("A")[0]["category"] == "own_transfer"
    assert svc.store.transactions("A", date(2026, 4, 30))[0]["category"] == "revenue"


def test_confirmed_missing_has_detection_date(client, svc, auth):
    response = client.post(
        "/api/v1/accounts/A/coverage-gaps",
        headers=auth,
        json={"start_date": "2026-04-01", "end_date": "2026-04-03", "known_at": "2026-05-01"},
    )
    assert response.status_code == 200
    assert any(d["coverage"] == "confirmed_missing" for d in svc.store.daily("A"))
    past = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    )
    assert not any("Подтверждённые пропуски" in f for f in past.quality_flags)


def test_event_reschedule_keeps_previous_knowledge(client, svc, auth):
    from conftest import OWNER
    from test_events_scenarios import make_event

    from cashflow_domain.schemas import EventCreate

    svc.add_event(EventCreate(event=make_event()), OWNER)
    response = client.patch(
        "/api/v1/events/invoice",
        headers=auth,
        json={"expected_date": "2026-05-25", "known_at": "2026-05-02"},
    )
    assert response.status_code == 200
    assert svc.events("A", date(2026, 4, 30))[0].expected_date == date(2026, 5, 10)
    assert svc.events("A", date(2026, 5, 2))[0].expected_date == date(2026, 5, 25)
