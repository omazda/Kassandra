from datetime import date

import pytest
from conftest import OWNER, csv_bytes, transaction

from apps.dashboard.calendar import (
    api_error_message,
    build_event_request,
    calendar_view,
    form_dates,
    save_message,
)
from cashflow_domain.schemas import ForecastRequest


def form(**changes):
    values = dict(
        account_id="A",
        as_of="2026-04-30",
        base=None,
        session_id="test-session",
        label="Счёт клиента",
        amount=300000.01,
        direction="in",
        category="revenue",
        due="2026-05-15",
        expected="2026-05-15",
        known="2026-04-30",
        replacement="additional",
    )
    return build_event_request(**{**values, **changes})


@pytest.mark.parametrize(
    ("asof", "expected"),
    [("2026-08-05", "2026-08-20"), ("2026-08-31", "2026-09-15"), ("2026-12-25", "2027-01-09")],
)
def test_form_dates_follow_selected_cut(asof, expected):
    assert form_dates(asof) == (asof, expected)


def test_repeat_save_cannot_duplicate_receipt_even_after_forecast_refresh(client, svc, auth):
    before = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    )
    body = form(base=before.model_dump(mode="json"))
    assert client.post("/api/v1/events", headers=auth, json=body).status_code == 200
    updated = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    )
    retried_body = form(base=updated.model_dump(mode="json"))
    assert body["event"] == retried_body["event"]
    assert client.post("/api/v1/events", headers=auth, json=retried_body).status_code == 200
    assert len(svc.events("A", date(2026, 4, 30))) == 1
    assert svc.store.account("A")["event_version"] == before.event_version + 1
    final = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    )
    assert (
        final.points[-1].closing_balance_minor - before.points[-1].closing_balance_minor == 30000001
    )
    assert svc.get_forecast(before.run_id) == before
    assert form(session_id="explicit-new-event")["event"]["id"] != body["event"]["id"]


def test_later_known_event_visible_in_register_but_never_leaks_into_forecast(client, svc, auth):
    before = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    )
    body = form(known="2026-05-02")
    assert client.post("/api/v1/events", headers=auth, json=body).status_code == 200
    selected = client.get("/api/v1/accounts/A/events?as_of=2026-04-30", headers=auth).json()
    register = client.get("/api/v1/accounts/A/events", headers=auth).json()
    assert selected == [] and len(register) == 1
    after = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    )
    assert before.points == after.points
    rows, _ = calendar_view(after.model_dump(mode="json"), selected, register, "A", "2026-04-30")
    row = next(r for r in rows if r["id"] == body["event"]["id"])
    assert row["inclusion"] == "Известно позже даты расчёта"
    assert "не включено" in save_message(body["event"], "2026-04-30")


def test_beyond_horizon_event_is_visible_with_explanation(client, auth):
    body = form(expected="2026-06-15")
    assert client.post("/api/v1/events", headers=auth, json=body).status_code == 200
    register = client.get("/api/v1/accounts/A/events", headers=auth).json()
    rows, _ = calendar_view(None, register, register, "A", "2026-04-30")
    assert rows[0]["inclusion"] == "За горизонтом 30 дней"
    assert "за горизонтом" in save_message(body["event"], "2026-04-30")


def test_register_works_without_a_successful_model_forecast(client, auth):
    body = form()
    assert body["base_run_id"] is None
    assert client.post("/api/v1/events", headers=auth, json=body).status_code == 200
    register = client.get("/api/v1/accounts/A/events", headers=auth).json()
    rows, _ = calendar_view(None, register, register, "A", "2026-04-30")
    assert rows[0]["inclusion"] == "Ожидает расчёта прогноза"
    assert rows[0]["amount"] == 300000.01


def test_stale_account_snapshot_is_never_used_for_new_receipt(svc):
    base = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    ).model_dump(mode="json")
    body = form(account_id="OTHER", base=base)
    assert body["event"]["account_id"] == "OTHER" and body["base_run_id"] is None
    assert calendar_view(base, [], [], "OTHER", "2026-04-30") == ([], [])


def test_api_rejects_receipt_bound_to_another_account_forecast(client, svc, auth):
    base = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    )
    svc.import_transactions(csv_bytes([transaction(account="OTHER")]), OWNER)
    body = form(account_id="OTHER")
    body["base_run_id"] = base.run_id
    response = client.post("/api/v1/events", headers=auth, json=body)
    assert response.status_code == 422
    assert svc.store.account("OTHER")["event_version"] == 0


def test_stale_or_wrong_direction_replacement_is_rejected(svc):
    base = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    ).model_dump(mode="json")
    target = next(e for e in base["events"] if e["direction"] == "out")
    with pytest.raises(ValueError, match="Направление и категория"):
        form(base=base, replacement=target["id"])
    with pytest.raises(ValueError, match="уже замещено"):
        form(base=base, replacement="missing-expectation")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"amount": None}, "Сумма"),
        ({"amount": -1}, "Сумма"),
        ({"amount": "1.001"}, "копейки"),
        ({"known": None}, "Известно на дату"),
        ({"label": " "}, "Название"),
    ],
)
def test_form_errors_identify_the_field(changes, message):
    with pytest.raises(ValueError, match=message):
        form(**changes)


def test_api_validation_errors_name_the_field():
    assert api_error_message(
        [
            {
                "loc": ["body", "event", "known_at"],
                "type": "date_from_datetime_parsing",
                "msg": "Invalid input",
            }
        ]
    ).startswith("Известно на дату:")
