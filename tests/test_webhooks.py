"""Webhooks: signed deliveries apply once to their own account, tests write nothing, journal."""

import json
from datetime import date

import pytest
from conftest import OWNER

from cashflow_services.webhooks import sample_payload, sign

AS_OF = date(2026, 4, 30)


def deliver(svc, hook, body, secret, delivery_id=None, bearer=False):
    headers = (
        {"authorization": f"Bearer {secret}"}
        if bearer
        else {"x-kassandra-signature": sign(secret, body)}
    )
    if delivery_id:
        headers["x-delivery-id"] = delivery_id
    return svc.receive_webhook(hook["id"], body, headers, "application/json")


def events(svc):
    return [e for e in svc.events("A", date(2026, 12, 31)) if e.source == "bank"]


def test_signed_bank_delivery_applies_once_and_is_journaled(svc):
    hook = svc.create_webhook("A", "bank", None, OWNER)
    assert hook["secret"].startswith("whsec_") and hook["path"] == f"/api/v1/hooks/{hook['id']}"
    assert "secret" not in svc.webhooks("A", OWNER)[0]
    body, _ = sample_payload("bank", "A", AS_OF)
    first = deliver(svc, hook, body, hook["secret"], delivery_id="d-1")
    assert first["status"] == "ok" and "новых 2" in first["summary"].lower()
    assert len(events(svc)) == 2
    again = deliver(svc, hook, body, hook["secret"], delivery_id="d-1")
    assert again["status"] == "duplicate" and len(events(svc)) == 2
    listed = svc.webhooks("A", OWNER)[0]
    assert listed["deliveries"] == 1 and listed["last_delivery"]["status"] == "ok"
    log = svc.webhook_deliveries("A", OWNER)
    assert log[0]["kind"] == "bank" and log[0]["delivery_id"] == "d-1" and not log[0]["test"]


def test_bad_signature_is_refused_and_bearer_secret_works(svc):
    hook = svc.create_webhook("A", "marketplace", "WB", OWNER)
    body, _ = sample_payload("marketplace", "A", AS_OF)
    with pytest.raises(PermissionError):
        svc.receive_webhook(hook["id"], body, {"x-kassandra-signature": "sha256=00"}, "")
    with pytest.raises(PermissionError):
        svc.receive_webhook(hook["id"], body, {}, "")
    assert deliver(svc, hook, body, hook["secret"], bearer=True)["status"] == "ok"


def test_statement_delivery_writes_only_its_own_account(svc):
    hook = svc.create_webhook("A", "statement", None, OWNER)
    last = svc.store.daily("A")[-1]
    balance = (last["balance_minor"] + 1_000_000) / 100
    operation = {
        "operation_id": "wh-1",
        "operation_date": "2026-05-29",
        "amount": "10000.00",
        "direction": "Поступление",
        "counterparty_inn": "0012345678",
        "counterparty_name": "Клиент",
        "payment_purpose": "Оплата по счёту",
        "balance_eod": f"{balance:.2f}",
    }
    body = json.dumps({"operations": [operation]}).encode()
    result = deliver(svc, hook, body, hook["secret"])
    assert "Операций новых: 1" in result["summary"]
    assert svc.store.daily("A")[-1]["date"] == "2026-05-29"
    foreign = json.dumps({"operations": [{**operation, "operation_id": "x", "company_id": "B"}]})
    with pytest.raises(ValueError, match="другому счёту"):
        deliver(svc, hook, foreign.encode(), hook["secret"])
    assert svc.webhook_deliveries("A", OWNER)[0]["status"] == "error"
    csv_body = (
        "operation_id;operation_date;amount;direction;counterparty_inn;counterparty_name;"
        "payment_purpose;balance_eod\nwh-1;2026-05-29;10000.00;Поступление;0012345678;Клиент;"
        f"Оплата по счёту;{balance:.2f}\n"
    ).encode()
    assert "повторов: 1" in deliver(svc, hook, csv_body, hook["secret"])["summary"]


def test_check_reports_real_deliveries_only_and_invents_nothing(svc):
    hook = svc.create_webhook("A", "1c", None, OWNER)
    waiting = svc.check_webhook(hook["id"], OWNER)
    # A fresh webhook has no data: the check must not «find» documents from a built-in sample.
    assert waiting["status"] == "waiting" and waiting["deliveries"] == 0
    assert "документ" not in waiting["summary"]
    assert svc.webhook_deliveries("A", OWNER) == []
    bank = svc.create_webhook("A", "bank", None, OWNER)
    body, _ = sample_payload("bank", "A", AS_OF)
    deliver(svc, bank, body, bank["secret"])
    working = svc.check_webhook(bank["id"], OWNER)
    assert working["status"] == "working" and working["deliveries"] == 1
    with pytest.raises(ValueError):
        deliver(svc, bank, b"{}", bank["secret"])
    assert svc.check_webhook(bank["id"], OWNER)["status"] == "failing"


def test_validate_checks_the_users_body_without_writing(svc):
    hook = svc.create_webhook("A", "bank", None, OWNER)
    body, _ = sample_payload("bank", "A", AS_OF)
    valid = svc.validate_webhook(hook["id"], OWNER, body, "application/json")
    assert valid["status"] == "valid" and "было бы 2" in valid["summary"]
    assert not events(svc) and svc.webhook_deliveries("A", OWNER) == []
    with pytest.raises(ValueError, match="Неверные данные"):
        svc.validate_webhook(hook["id"], OWNER, b'{"loans": [{"contract": 1}]}', "")
    with pytest.raises(ValueError, match="Вставьте"):
        svc.validate_webhook(hook["id"], OWNER, b"  ", "")


def test_rotate_and_delete(svc):
    hook = svc.create_webhook("A", "ofd", None, OWNER)
    body, _ = sample_payload("ofd", "A", AS_OF)
    new = svc.rotate_webhook(hook["id"], OWNER)["secret"]
    with pytest.raises(PermissionError):
        deliver(svc, hook, body, hook["secret"])
    assert deliver(svc, hook, body, new)["status"] == "ok"
    with pytest.raises(PermissionError):
        svc.create_webhook("A", "ofd", None, {"id": "bob", "accounts": []})
    svc.delete_webhook(hook["id"], OWNER)
    assert svc.webhooks("A", OWNER) == []
    with pytest.raises(ValueError, match="не найден"):
        deliver(svc, hook, body, new)


def test_http_receiving_end_needs_no_session(svc):
    from fastapi.testclient import TestClient

    from apps.api.main import create_app

    api = TestClient(create_app(svc, users={"t": OWNER}, auth_mode="token"))
    auth = {"Authorization": "Bearer t"}
    hook = api.post("/api/v1/accounts/A/webhooks", json={"kind": "invoices"}, headers=auth).json()
    assert len(api.get("/api/v1/webhooks/kinds", headers=auth).json()) == 7
    body, _ = sample_payload("invoices", "A", AS_OF)
    ok = api.post(
        hook["path"],
        content=body,
        headers={
            "X-Kassandra-Signature": sign(hook["secret"], body),
            "Content-Type": "application/json",
        },
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "ok"
    bad = api.post(hook["path"], content=body, headers={"X-Kassandra-Signature": "sha256=1"})
    assert bad.status_code == 401
    broken = api.post(
        hook["path"],
        content=b"{not json",
        headers={"X-Kassandra-Signature": sign(hook["secret"], b"{not json")},
    )
    assert broken.status_code == 422
    assert api.get("/api/v1/accounts/A/webhooks", headers=auth).json()[0]["deliveries"] == 2
