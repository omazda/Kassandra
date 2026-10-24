from datetime import date

from conftest import csv_bytes, transaction

from cashflow_domain.schemas import ForecastRequest


def test_api_import_forecast_event_scenario_plan_fact(client, auth, caplog):
    assert client.get("/health").json()["status"] == "ok"
    imported = client.post(
        "/api/v1/imports/transactions",
        headers=auth,
        files={
            "file": (
                "test.csv",
                csv_bytes([transaction(account="UPLOAD", purpose="PRIVATE PAYMENT TEXT")]),
                "text/csv",
            )
        },
    )
    assert imported.status_code == 200
    accounts = client.get("/api/v1/accounts", headers=auth).json()
    assert {a["id"] for a in accounts} == {"A", "UPLOAD"}
    body = {"account_id": "A", "as_of": "2026-04-30", "model": "weekday"}
    result = client.post(
        "/api/v1/forecasts", headers={**auth, "Idempotency-Key": "first"}, json=body
    )
    assert result.status_code == 200, result.text
    base = result.json()
    assert len(base["points"]) == 30
    repeated = client.post(
        "/api/v1/forecasts", headers={**auth, "Idempotency-Key": "first"}, json=body
    ).json()
    assert repeated["run_id"] == base["run_id"]
    conflict = client.post(
        "/api/v1/forecasts",
        headers={**auth, "Idempotency-Key": "first"},
        json={**body, "model": "mean"},
    )
    assert conflict.status_code == 409
    scenario = client.post(
        "/api/v1/scenarios/evaluate",
        headers=auth,
        json={
            "base_run_id": base["run_id"],
            "kind": "extra_expense",
            "amount_minor": 12345,
            "expense_date": "2026-05-10",
        },
    )
    assert scenario.status_code == 200
    assert scenario.json()["delta_end_minor"] == -12345
    plan = client.get("/api/v1/plan-fact", headers=auth, params={"run_id": base["run_id"]})
    assert plan.status_code == 200 and len(plan.json()["rows"]) == 30
    assert "PRIVATE PAYMENT TEXT" not in caplog.text and "alice-token" not in caplog.text


def test_account_authorization_all_paths(client, svc, auth):
    body = {"account_id": "A", "as_of": "2026-04-30", "model": "structural"}
    base = client.post("/api/v1/forecasts", headers=auth, json=body).json()
    bob = {"Authorization": "Bearer bob-token"}
    assert client.get("/api/v1/accounts", headers=bob).json() == []
    for path in ["transactions", "daily", "quality", "events?as_of=2026-04-30", "forecasts"]:
        assert client.get(f"/api/v1/accounts/A/{path}", headers=bob).status_code == 403
    assert client.get(f"/api/v1/forecasts/{base['run_id']}", headers=bob).status_code == 403
    assert (
        client.get("/api/v1/plan-fact", headers=bob, params={"run_id": base["run_id"]}).status_code
        == 403
    )
    assert client.post("/api/v1/forecasts", headers=bob, json=body).status_code == 403
    assert (
        client.post(
            "/api/v1/scenarios/evaluate",
            headers=bob,
            json={"base_run_id": base["run_id"], "kind": "revenue_drop"},
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/api/v1/transactions/A/in-0/category", headers=bob, json={"category": "unknown"}
        ).status_code
        == 403
    )
    assert client.get("/api/v1/accounts").status_code == 403


def test_stale_versions_and_paginated_operations(client, auth):
    result = client.post(
        "/api/v1/forecasts",
        headers=auth,
        json={"account_id": "A", "as_of": "2026-04-30", "data_version": 999},
    )
    assert result.status_code == 409
    first = client.get("/api/v1/accounts/A/transactions?page_size=5&page=1", headers=auth).json()
    second = client.get("/api/v1/accounts/A/transactions?page_size=5&page=2", headers=auth).json()
    assert len(first["items"]) == len(second["items"]) == 5
    assert not {t["operation_id"] for t in first["items"]} & {
        t["operation_id"] for t in second["items"]
    }


def test_incremental_import_creates_version_without_fitting(client, svc, auth):
    old = svc.forecast(ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural"))
    new_tx = transaction(operation="update", day="2026-05-29", balance="9999.99")
    result = client.post(
        "/api/v1/imports/transactions",
        headers=auth,
        files={"file": ("update.csv", csv_bytes([new_tx]), "text/csv")},
    ).json()
    assert result["auto_refresh"][0]["status"] == "created"
    fresh = svc.get_forecast(result["auto_refresh"][0]["run_id"])
    assert fresh.data_version == old.data_version + 1
    assert svc.get_forecast(old.run_id) == old


def test_llm_off_does_not_require_or_expose_key(monkeypatch):
    from cashflow_services.llm import LLMClient, LLMSettings

    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert not LLMClient(LLMSettings()).settings.enabled
    assert "SECRET" not in repr(LLMSettings(api_key="SECRET"))


def test_demo_does_not_expose_private_imports(svc):
    from fastapi.testclient import TestClient

    from apps.api.main import create_app

    c = TestClient(create_app(svc, users={}, auth_mode="demo"))
    assert c.get("/api/v1/accounts").json() == []
    assert c.get("/api/v1/accounts/A/transactions").status_code == 403
    assert (
        c.post(
            "/api/v1/imports/transactions",
            files={"file": ("p.csv", csv_bytes([transaction()]), "text/csv")},
        ).status_code
        == 403
    )


def test_api_new_scenarios_top_clients_and_explanation(client, auth):
    bob = {"Authorization": "Bearer bob-token"}
    body = {"account_id": "A", "as_of": "2026-04-30", "model": "mean"}
    base = client.post("/api/v1/forecasts", headers=auth, json=body).json()
    top = client.get(
        "/api/v1/accounts/A/top-clients",
        headers=auth,
        params={"as_of": "2026-04-30", "limit": 3, "run_id": base["run_id"]},
    )
    assert top.status_code == 200 and len(top.json()) == 2
    with_events = [r for r in top.json() if r["upcoming_events"]]
    assert [r["counterparty_inn"] for r in with_events] == ["0011111111"]
    client_inn = with_events[0]["counterparty_inn"]
    assert (
        client.get(
            "/api/v1/accounts/A/top-clients", headers=bob, params={"as_of": "2026-04-30"}
        ).status_code
        == 403
    )
    payloads = [
        {"kind": "fixed_cost_increase", "fixed_cost_pct": 15},
        {"kind": "client_delay", "counterparty": client_inn, "delay_days": 21},
        {
            "kind": "combined",
            "parts": [
                {"kind": "revenue_drop", "revenue_drop_pct": 10},
                {"kind": "fixed_cost_increase", "fixed_cost_pct": 5},
            ],
        },
    ]
    for payload in payloads:
        res = client.post(
            "/api/v1/scenarios/evaluate",
            headers=auth,
            json={"base_run_id": base["run_id"], **payload},
        )
        assert res.status_code == 200, res.text
        scenario_id = res.json()["scenario_id"]
        explained = client.get(f"/api/v1/scenarios/{scenario_id}/explanation", headers=auth)
        assert explained.status_code == 200 and "text" in explained.json()
        assert (
            client.get(f"/api/v1/scenarios/{scenario_id}/explanation", headers=bob).status_code
            == 403
        )
    bad = client.post(
        "/api/v1/scenarios/evaluate",
        headers=auth,
        json={
            "base_run_id": base["run_id"],
            "kind": "client_delay",
            "counterparty": "x",
            "delay_days": 90,
        },
    )
    assert bad.status_code == 422
    unknown = client.post(
        "/api/v1/scenarios/evaluate",
        headers=auth,
        json={
            "base_run_id": base["run_id"],
            "kind": "client_delay",
            "counterparty": "none",
            "delay_days": 5,
        },
    )
    assert unknown.status_code == 422
    forecast = client.get(f"/api/v1/forecasts/{base['run_id']}/explanation", headers=auth)
    assert forecast.status_code == 200 and forecast.json()["has_gap"] in {True, False}
    assert (
        client.get(f"/api/v1/forecasts/{base['run_id']}/explanation", headers=bob).status_code
        == 403
    )
