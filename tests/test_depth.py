"""Second pass on the API: one owner per flow, statement reconciliation, overdue obligations,
downside view, risky counterparties and the typed contract of every v1.1 endpoint."""

from datetime import date

import pytest
from conftest import OWNER, csv_bytes, transaction

from cashflow_domain.engine import flow_shares, net_known_events
from cashflow_domain.feeds import ContractSpec, InvoiceSpec
from cashflow_domain.schemas import EventCreate, ForecastRequest, PlannedEvent

AS_OF = date(2026, 4, 30)


def base(svc, **kw):
    return svc.forecast(ForecastRequest(account_id="A", as_of=AS_OF, model="weekday", **kw))


def manual(svc, event_id, day, amount, direction="in", **kw):
    event = PlannedEvent(
        id=event_id,
        account_id="A",
        component_id="manual",
        category="customer_payment" if direction == "in" else "operating_expense",
        amount_minor=amount,
        direction=direction,
        due_date=day,
        expected_date=day,
        known_at=kw.pop("known_at", AS_OF),
        source="manual",
        status="confirmed",
        **kw,
    )
    return svc.add_event(EventCreate(event=event), OWNER)


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    path.write_text('{"0033333333": {"status": "bankrupt", "name": "Должник"}}', encoding="utf-8")
    monkeypatch.setenv("CASHFLOW_REGISTRY_DEMO", str(path))
    monkeypatch.delenv("DADATA_API_KEY", raising=False)


# ------------------------------------------------------------ one owner per flow


def test_netting_takes_only_the_group_share_and_keeps_the_excess():
    residuals = [[100.0, 0.0, 40.0]] * 30
    shares = {(0, "clients"): 0.25, (0, "cards"): 0.75, (2, "suppliers"): 1.0}
    event = PlannedEvent(
        id="i",
        account_id="A",
        component_id="invoice:x",
        category="customer_payment",
        amount_minor=10_000,
        direction="in",
        due_date=date(2026, 5, 10),
        expected_date=date(2026, 5, 10),
        known_at=AS_OF,
        source="invoice",
        status="confirmed",
    )
    rows, report = net_known_events(residuals, [event], AS_OF, shares)
    # Clients are a quarter of 3 000 expected: 750 displaced, the other 9 250 are extra money.
    assert report["displaced_minor"] == 750 and report["groups"][0]["capacity_minor"] == 750
    assert rows[0] == [75.0, 0.0, 40.0]
    manual_event = event.model_copy(update={"source": "manual"})
    assert net_known_events(residuals, [manual_event], AS_OF, shares)[1] is None


def test_flow_shares_follow_the_history(svc):
    shares = flow_shares(svc.store.transactions("A", AS_OF), AS_OF)
    revenue = {g: v for (s, g), v in shares.items() if s == 0}
    assert abs(sum(revenue.values()) - 1) < 1e-9 and shares[(2, "fixed")] == pytest.approx(1.0)


def test_fast_refresh_matches_a_full_forecast(svc):
    forecast = base(svc)
    spec = ContractSpec(
        contract_id="9",
        direction="in",
        total_minor=600_000,
        signed_date=AS_OF,
        prepayment_pct=50,
        delivery_date=date(2026, 5, 18),
        known_at=AS_OF,
    )
    svc.add_contract("A", spec, OWNER)
    fast = svc.refresh(forecast.run_id, OWNER)
    assert fast["mode"] == "events_only" and fast["forecast"]["netting"]["displaced_minor"] > 0
    full = base(svc)
    gaps = [
        abs(a["closing_balance_minor"] - b.closing_balance_minor)
        for a, b in zip(fast["forecast"]["points"], full.points, strict=True)
    ]
    # Same as-of and register: the event layer rebuilt without the model equals a full run.
    assert max(gaps) <= 30
    assert fast["forecast"]["risk"]["first_gap_date"] == (
        str(full.risk.first_gap_date) if full.risk.first_gap_date else None
    )


# ------------------------------------------------------------ overdue and downside


def test_overdue_obligation_stays_due_and_late_receipt_is_not_counted(svc):
    regular_in = sum(p.event_in_minor for p in base(svc).points)
    manual(svc, "late-out", date(2026, 4, 24), 700_000, direction="out")
    manual(svc, "late-in", date(2026, 4, 24), 900_000)
    forecast = base(svc)
    treatment = {o["event_id"]: (o["treatment"], o["new_date"]) for o in forecast.overdue_events}
    # 1 May is a holiday, 2–3 May a weekend: the debt is due on the next working day.
    assert treatment == {
        "late-out": ("rolled_forward", "2026-05-04"),
        "late-in": ("excluded", None),
    }
    day = next(p for p in forecast.points if p.date == date(2026, 5, 4))
    assert day.event_out_minor == 700_000
    assert sum(p.event_in_minor for p in forecast.points) == regular_in


def test_stress_removes_promises_and_raises_an_alert(svc):
    manual(svc, "spend", date(2026, 5, 6), 30_000_000, direction="out")
    manual(svc, "promise", date(2026, 5, 5), 30_000_000, certainty="promised")
    forecast = base(svc)
    stress = forecast.risk.stress
    assert forecast.risk.first_gap_date is None
    assert (
        stress["excluded"][0]["event_id"] == "promise" and stress["first_gap_date"] == "2026-05-06"
    )
    kinds = [a["kind"] for a in svc.alerts("A", OWNER, forecast.run_id)["alerts"]]
    assert "stress_gap" in kinds


def test_risky_counterparty_is_a_downside_and_an_alert(svc, registry):
    manual(svc, "risky", date(2026, 5, 8), 400_000, counterparty_inn="0033333333")
    forecast = base(svc)
    assert forecast.risk.stress["excluded"][0]["reason"] == "контрагент с риском в реестре"
    alert = next(
        a
        for a in svc.alerts("A", OWNER, forecast.run_id)["alerts"]
        if a["kind"] == "risky_counterparty"
    )
    assert (
        alert["severity"] == "critical"
        and alert["action"]["scenario"]["counterparty"] == "0033333333"
    )


def test_probability_stays_with_registered_events(svc, monkeypatch):
    import json

    rows = [
        {
            "recipe": "weekday|weekday-v1|calendar=True",
            "account_id": str(i % 5),
            "available_to": "2026-01-01",
            "normalized_balance_error": [0.0] * 30,
        }
        for i in range(25)
    ]
    (svc.artifacts / "error_bank.json").write_text(json.dumps(rows))
    manual(svc, "e", date(2026, 5, 7), 1000)
    risk = base(svc).risk
    assert risk.probability_30d is not None
    assert "события реестра учтены как надёжные" in risk.probability_source


# ------------------------------------------------------------ reconciliation


def test_invoice_paid_in_a_new_statement_is_linked_automatically(svc):
    spec = InvoiceSpec(
        number="777",
        direction="in",
        issue_date=date(2026, 5, 20),
        due_date=date(2026, 5, 27),
        amount_minor=123_456,
        counterparty_inn="0022222222",
        known_at=date(2026, 5, 20),
    )
    event_id = svc.add_invoices("A", [spec], OWNER)["events"][0]["id"]
    paid = transaction(
        operation="pay-777",
        day="2026-05-29",
        amount="1234.56",
        balance="1.00",
        inn="0022222222",
        purpose="Оплата по счету № 777 от 20.05.2026 за услуги",
    )
    report = svc.import_transactions(csv_bytes([paid]), OWNER)
    assert report["reconciliation"] == {"A": {"linked": 1, "needs_review": 0}}
    event = next(e for e in svc.events("A", date(2026, 5, 29)) if e.id == event_id)
    assert event.status == "paid"
    again = svc.reconcile("A", OWNER)
    assert again["linked"] == [] and again["checked_events"] == 0


def test_ambiguous_payments_go_to_review(svc):
    manual(svc, "daily", date(2026, 5, 12), 50_001, counterparty_inn="0012345678")
    result = svc.reconcile("A", OWNER, dry_run=True)
    assert result["linked"] == []
    review = result["needs_review"][0]
    assert review["event_id"] == "daily" and len(review["candidates"]) == 3
    assert svc.events("A", date(2026, 5, 28))[0].status == "confirmed"


# ------------------------------------------------------------ typed contract of every endpoint

ONE_C = """1CClientBankExchange
РасчСчет=40702810000000000001
СекцияРасчСчет
ДатаНачала=01.06.2026
РасчСчет=40702810000000000001
НачальныйОстаток=1000.00
КонецРасчСчет
СекцияДокумент=Платежное поручение
Номер=1
Дата=01.06.2026
Сумма=10.00
ПлательщикСчет=40702810999999999999
Плательщик1=Покупатель
ПолучательСчет=40702810000000000001
НазначениеПлатежа=Оплата
КонецДокумента
КонецФайла
"""


def test_every_v11_endpoint_matches_its_response_model(client, auth, registry):
    def ok(response):
        assert response.status_code == 200, response.text
        return response.json()

    get = lambda path, **params: ok(client.get(path, headers=auth, params=params))  # noqa: E731
    post = lambda path, body=None, **kw: ok(client.post(path, headers=auth, json=body, **kw))  # noqa: E731
    forecast = post(
        "/api/v1/forecasts", {"account_id": "A", "as_of": "2026-04-30", "model": "weekday"}
    )
    run = forecast["run_id"]
    get("/api/v1/dictionaries")
    get("/api/v1/accounts/A/categories/summary", date_from="2026-02-01")
    get("/api/v1/accounts/A/recurring", as_of="2026-04-30")
    get("/api/v1/accounts/A/tax-calendar", as_of="2026-04-30", run_id=run)
    post("/api/v1/accounts/A/tax-calendar/apply", {"base_run_id": run})
    contract = post(
        "/api/v1/accounts/A/contracts",
        {
            "contract_id": "K-1",
            "direction": "in",
            "total_minor": 300_000,
            "signed_date": "2026-04-01",
            "prepayment_pct": 50,
            "delivery_date": "2026-05-12",
            "known_at": "2026-04-30",
            "counterparty_inn": "0033333333",
        },
    )
    assert contract["skipped_past_due"][0]["stage"] == "предоплата"
    invoice = post(
        "/api/v1/accounts/A/invoices",
        {
            "number": "5",
            "direction": "out",
            "issue_date": "2026-04-28",
            "payment_terms_days": 7,
            "amount_minor": 1000,
            "base_run_id": run,
        },
    )
    assert invoice["payment_dates"][0]["level"] == "due_date"
    files = {
        "file": (
            "i.csv",
            b"number;issue_date;amount;direction;payment_terms_days\n9;2026-04-29;10;in;3\n",
            "text/csv",
        )
    }
    post("/api/v1/accounts/A/imports/invoices", files=files, params={"known_at": "2026-04-30"})
    post(
        "/api/v1/accounts/A/integrations/bank",
        {"known_at": "2026-04-30", "payroll": [{"date": "2026-05-08", "amount_minor": 10}]},
    )
    post(
        "/api/v1/accounts/A/integrations/marketplace",
        {
            "known_at": "2026-04-30",
            "marketplace": "M",
            "payouts": [{"date": "2026-05-08", "amount_minor": 10}],
        },
    )
    post(
        "/api/v1/accounts/A/integrations/ofd",
        {"known_at": "2026-04-30", "days": [{"date": "2026-04-30", "card_minor": 1000}]},
    )
    post("/api/v1/events/parse", {"text": "аренда 50 тыс 15-го", "today": "2026-04-30"})
    assert get("/api/v1/counterparties/0033333333/check")["source"] == "demo_registry"
    get("/api/v1/accounts/A/counterparty-risks", as_of="2026-04-30")
    post("/api/v1/accounts/A/reconcile", params={"dry_run": True})
    refreshed = post(f"/api/v1/forecasts/{run}/refresh")
    assert refreshed["mode"] in ("events_only", "events_full")
    alerts = get("/api/v1/accounts/A/alerts", run_id=refreshed["forecast"]["run_id"])
    assert any(a["kind"] == "risky_counterparty" for a in alerts["alerts"])
    get("/api/v1/accounts/A/sources")
    get("/api/v1/plan-fact", run_id=run)
    assert get("/api/v1/plan-fact", run_id=run, granularity="week")["weeks"]
    one_c = {"file": ("s.txt", ONE_C.encode("cp1251"), "text/plain")}
    assert post("/api/v1/accounts/ONEC/imports/1c", files=one_c)["source"]["closing_check"] is None


def test_every_v10_endpoint_matches_its_response_model(client, auth):
    def ok(response):
        assert response.status_code == 200, response.text
        return response.json()

    assert ok(client.get("/health"))["status"] == "ok"
    accounts = ok(client.get("/api/v1/accounts", headers=auth))
    assert accounts[0]["quality"]["operations"] > 0
    page = ok(client.get("/api/v1/accounts/A/transactions", headers=auth, params={"page_size": 5}))
    assert len(page["items"]) == 5 and page["total"] > 5
    ok(client.get("/api/v1/accounts/A/daily", headers=auth))
    ok(client.get("/api/v1/accounts/A/quality", headers=auth))
    operation = page["items"][0]
    ok(
        client.patch(
            f"/api/v1/transactions/A/{operation['operation_id']}/category",
            headers=auth,
            json={"category": "customer_payment", "known_at": operation["operation_date"]},
        )
    )
    ok(
        client.post(
            "/api/v1/accounts/A/coverage-gaps",
            headers=auth,
            json={"start_date": "2026-01-03", "end_date": "2026-01-04", "known_at": "2026-01-05"},
        )
    )
    forecast = ok(
        client.post(
            "/api/v1/forecasts",
            headers=auth,
            json={"account_id": "A", "as_of": "2026-04-30", "model": "weekday"},
        )
    )
    ok(client.get("/api/v1/accounts/A/forecasts", headers=auth))
    event = {
        "id": "typed-1",
        "account_id": "A",
        "component_id": "manual",
        "category": "customer_payment",
        "amount_minor": 50_001,
        "direction": "in",
        "due_date": "2026-05-05",
        "expected_date": "2026-05-05",
        "known_at": "2026-04-30",
        "source": "manual",
        "status": "confirmed",
    }
    ok(client.post("/api/v1/events", headers=auth, json={"event": event}))
    ok(client.get("/api/v1/accounts/A/events", headers=auth, params={"as_of": "2026-04-30"}))
    ok(
        client.patch(
            "/api/v1/events/typed-1",
            headers=auth,
            json={"expected_date": "2026-05-06", "known_at": "2026-05-01"},
        )
    )
    paid = next(t for t in page["items"] if t["direction"] == "in")
    linked = ok(
        client.post(
            "/api/v1/events/typed-1/payments",
            headers=auth,
            json={
                "operation_id": paid["operation_id"],
                "amount_minor": 50_001,
                "known_at": paid["operation_date"],
            },
        )
    )
    assert linked["status"] == "linked"
    clients = ok(
        client.get(
            "/api/v1/accounts/A/top-clients",
            headers=auth,
            params={"as_of": "2026-04-30", "run_id": forecast["run_id"]},
        )
    )
    assert clients[0]["share"] > 0 and "upcoming_events" in clients[0]
    registry = "id;account_id;component_id;category;amount_minor;direction;due_date;expected_date;known_at;status\n"
    registry += "csv-1;A;manual;supplier;1000;out;2026-05-07;2026-05-07;2026-04-30;confirmed\n"
    files = {"file": ("e.csv", registry.encode(), "text/csv")}
    assert (
        ok(client.post("/api/v1/imports/events", headers=auth, files=files))[0]["source"] == "csv"
    )
    statement = csv_bytes([transaction(account="NEW", operation="n1")])
    report = ok(
        client.post(
            "/api/v1/imports/transactions",
            headers=auth,
            files={"file": ("s.csv", statement, "text/csv")},
        )
    )
    assert report["inserted"] == 1 and report["reconciliation"] == {}
