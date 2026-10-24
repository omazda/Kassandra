from datetime import date

import pytest
from conftest import OWNER, csv_bytes, transaction

from cashflow_domain.feeds import BankFeed, ContractSpec, InvoiceSpec, MarketplaceFeed, OfdFeed
from cashflow_domain.schemas import Category, EventCreate, ForecastRequest, PlannedEvent
from cashflow_services import integrations
from cashflow_services.integrations import (
    CounterpartyRegistry,
    client_delays,
    inn_checksum_valid,
    parse_client_bank_exchange,
    parse_event_text,
)

AS_OF = date(2026, 4, 30)


def base(svc, **kw):
    return svc.forecast(ForecastRequest(account_id="A", as_of=AS_OF, model="weekday", **kw))


# ------------------------------------------------------------ manual input from a phrase


def test_phrases_from_the_case_become_complete_drafts():
    today = date(2026, 4, 26)
    promise = parse_event_text("Клиент обещал заплатить 300 000 рублей 20-го", today)
    assert promise["complete"]
    assert promise["draft"] | {"label": None} == {
        "direction": "in",
        "amount_minor": 30_000_000,
        "expected_date": "2026-05-20",
        "category": "customer_payment",
        "label": None,
        "certainty": "promised",
        "counterparty_name": None,
    }
    purchase = parse_event_text("В декабре куплю оборудование за 1 млн рублей", today)
    assert purchase["draft"]["direction"] == "out"
    assert purchase["draft"]["amount_minor"] == 100_000_000
    assert purchase["draft"]["expected_date"] == "2026-12-01"
    assert any("только месяц" in n for n in purchase["notes"])


def test_phrase_parser_dates_amounts_and_gaps():
    today = date(2026, 4, 26)
    rent = parse_event_text("Аренда 150 тыс. 5 мая", today)["draft"]
    assert (rent["direction"], rent["category"], rent["amount_minor"], rent["expected_date"]) == (
        "out",
        "rent",
        15_000_000,
        "2026-05-05",
    )
    # 1 May is a public holiday: the money is expected on the next working day.
    holiday = parse_event_text("Покупатель «Ромашка» переведёт 2,5 млн 01.05", today)
    assert holiday["draft"]["expected_date"] == "2026-05-04"
    assert holiday["draft"]["amount_minor"] == 250_000_000
    assert holiday["draft"]["counterparty_name"] == "Ромашка"
    unclear = parse_event_text("клиент заплатит завтра", today)
    assert not unclear["complete"] and unclear["missing"] == ["amount_minor"]


# ------------------------------------------------------------ contracts and invoices


def test_contract_schedule_enters_the_forecast_once(svc):
    before = base(svc)
    spec = ContractSpec(
        contract_id="17/26",
        direction="in",
        total_minor=100_000_000,
        signed_date=AS_OF,
        prepayment_pct=30,
        prepayment_days=2,
        delivery_date=date(2026, 5, 15),
        deferral_days=5,
        counterparty_inn="0012345678",
        counterparty_name="Обезличенный клиент",
        known_at=AS_OF,
    )
    result = svc.add_contract("A", spec, OWNER)
    dates = [(e["expected_date"], e["amount_minor"], e["category"]) for e in result["events"]]
    # 2 May is a Saturday after the 1 May holiday; the remainder comes 5 days after delivery.
    assert dates == [
        ("2026-05-04", 30_000_000, "contract_prepayment"),
        ("2026-05-20", 70_000_000, "contract_settlement"),
    ]
    assert all(e["source"] == "contract" and e["certainty"] == "agreed" for e in result["events"])
    after = base(svc)
    # The client's usual 500.01 a day is what the contract pays: the 30 days of it are displaced.
    assert after.netting["displaced_minor"] == 30 * 50_001
    assert after.netting["groups"] == [
        {
            "stream": "revenue",
            "group": "clients",
            "events_minor": 100_000_000,
            "capacity_minor": 1_500_030,
            "displaced_minor": 1_500_030,
        }
    ]
    assert (
        after.points[-1].closing_balance_minor - before.points[-1].closing_balance_minor
        == 100_000_000 - 1_500_030
    )
    again = svc.add_contract("A", spec, OWNER)
    assert (again["created"], again["unchanged"]) == (0, 2)


def test_contract_validates_schedule_sum():
    with pytest.raises(ValueError, match="графика"):
        ContractSpec(
            contract_id="1",
            direction="out",
            total_minor=1000,
            signed_date=AS_OF,
            schedule=[{"date": "2026-05-10", "amount_minor": 999}],
        )


def test_invoice_expected_date_uses_client_delay_from_history():
    txs = [
        dict(
            direction="in",
            operation_date=f"2026-03-{d:02d}",
            payment_purpose=f"Оплата по счету №{d} от 01.03.2026 за товар",
            counterparty_inn="0011111111",
            counterparty_name="Клиент",
        )
        for d in (19, 21, 23)
    ]
    own, everyone = client_delays(txs, "0011111111", AS_OF)
    assert own == [18, 20, 22] and everyone == own
    spec = InvoiceSpec(
        number="45",
        direction="in",
        issue_date=date(2026, 4, 28),
        payment_terms_days=5,
        amount_minor=500_000,
        counterparty_inn="0011111111",
    )
    event, delay = integrations.invoice_event(spec, "A", AS_OF, txs)
    # Due on 3 May (Sunday) but this client pays ~20 days after the invoice: 18 May.
    assert delay["level"] == "client" and delay["median_days"] == 20
    assert event.expected_date == date(2026, 5, 18) and event.due_date == date(2026, 5, 3)
    assert event.source == "invoice" and event.document_ref == "Счёт №45 от 28.04.2026"


def test_overdue_invoice_is_expected_right_after_the_date_of_knowledge(svc):
    spec = InvoiceSpec(
        number="9",
        direction="in",
        issue_date=date(2026, 3, 1),
        due_date=date(2026, 3, 10),
        amount_minor=1_000_000,
        paid_minor=400_000,
        known_at=AS_OF,
    )
    result = svc.add_invoices("A", [spec], OWNER)
    assert result["payment_dates"][0]["overdue"] is True
    event = result["events"][0]
    assert event["amount_minor"] == 600_000 and event["expected_date"] == "2026-05-04"


def test_invoice_csv_import(svc):
    payload = (
        "number;issue_date;due_date;amount;direction;counterparty_inn;counterparty_name\n"
        "101;2026-04-28;2026-05-12;15000,50;выставлен;0012345678;Клиент\n"
        "П-7;2026-04-29;2026-05-06;3200;получен;;Поставщик\n"
    ).encode("cp1251")
    result = svc.import_invoices("A", payload, OWNER, known_at=AS_OF)
    assert result["created"] == 2
    assert {e["direction"] for e in result["events"]} == {"in", "out"}
    assert {e["amount_minor"] for e in result["events"]} == {1_500_050, 320_000}


# ------------------------------------------------------------ bank, marketplace, cash registers


def test_bank_order_replaces_the_regular_rent_assumption(svc):
    forecast = base(svc)
    rent = next(e for e in forecast.events if e.category == "rent")
    feed = BankFeed(
        base_run_id=forecast.run_id,
        payment_orders=[
            {
                "number": "311",
                "date": str(rent.expected_date),
                "amount_minor": rent.amount_minor,
                "counterparty_inn": rent.counterparty_inn,
                "purpose": "Аренда помещения за май",
            }
        ],
        loans=[
            {
                "contract": "КД-5",
                "schedule": [
                    {"date": "2026-05-20", "principal_minor": 1_000_000, "interest_minor": 50_000}
                ],
            }
        ],
        payroll=[{"date": "2026-05-08", "amount_minor": 2_000_000, "kind": "advance"}],
    )
    result = svc.add_feed("A", feed, OWNER, "bank")
    assert result["created"] == 3
    assert [r["replaced_id"] for r in result["replaced_assumptions"]] == [rent.id]
    after = base(svc)
    assert not any(e.id == rent.id for e in after.events)
    # The rent is counted once; only the loan and the payroll advance are new.
    delta = after.points[-1].closing_balance_minor - forecast.points[-1].closing_balance_minor
    assert delta == -(1_050_000 + 2_000_000)
    assert {e.source for e in after.events if e.source not in ("regular",)} == {"bank"}


def test_marketplace_and_ofd_feeds(svc):
    market = svc.add_feed(
        "A",
        MarketplaceFeed(
            marketplace="Маркетплейс",
            payouts=[{"date": "2026-05-06", "amount_minor": 777_000, "report_ref": "R-1"}],
            known_at=AS_OF,
        ),
        OWNER,
        "marketplace",
    )
    assert market["events"][0]["category"] == "marketplace_payout"
    ofd = OfdFeed(
        days=[
            {"date": "2026-04-29", "card_minor": 10_000_000},
            {"date": "2026-04-30", "card_minor": 10_000_000, "cash_minor": 5},
        ],
        known_at=AS_OF,
    )
    result = svc.add_feed("A", ofd, OWNER, "ofd")
    # 29 April settles on 30 April (already known); 30 April settles after the May holidays.
    assert [(e["expected_date"], e["amount_minor"]) for e in result["events"]] == [
        ("2026-05-04", 9_820_000)
    ]


# ------------------------------------------------------------ 1С «Клиент-банк»

ONE_C = """1CClientBankExchange
ВерсияФормата=1.03
Кодировка=Windows
РасчСчет=40702810000000000001
СекцияРасчСчет
ДатаНачала=01.06.2026
ДатаКонца=02.06.2026
РасчСчет=40702810000000000001
НачальныйОстаток=1000.00
КонечныйОстаток=1300.00
КонецРасчСчет
СекцияДокумент=Платежное поручение
Номер=12
Дата=01.06.2026
Сумма=500.00
ПлательщикСчет=40702810999999999999
Плательщик=ИНН 7707083893 ПАО Покупатель
Плательщик1=ПАО Покупатель
ПлательщикИНН=7707083893
ПолучательСчет=40702810000000000001
ДатаПоступило=01.06.2026
НазначениеПлатежа=Оплата по счету №5 от 25.05.2026 за товар
КонецДокумента
СекцияДокумент=Платежное поручение
Номер=88
Дата=02.06.2026
Сумма=200.00
ПлательщикСчет=40702810000000000001
ПолучательСчет=40702810888888888888
Получатель1=ООО Арендодатель
ПолучательИНН=7700000000
ДатаСписано=02.06.2026
НазначениеПлатежа=Аренда за июнь
КонецДокумента
КонецФайла
"""


def test_client_bank_exchange_restores_end_of_day_balances(svc):
    payload, info = parse_client_bank_exchange(ONE_C.encode("cp1251"), "ONEC")
    assert info["closing_check"] == "ok" and info["documents"] == 2
    report = svc.import_1c("ONEC", ONE_C.encode("cp1251"), OWNER)
    assert report["inserted"] == 2
    days = {d["date"]: d["balance_minor"] for d in svc.store.daily("ONEC")}
    assert days == {"2026-06-01": 150_000, "2026-06-02": 130_000}
    txs = svc.store.transactions("ONEC")
    assert {(t["direction"], t["counterparty_name"], t["category"]) for t in txs} == {
        ("in", "ПАО Покупатель", "customer_payment"),
        ("out", "ООО Арендодатель", "rent"),
    }
    assert svc.import_1c("ONEC", ONE_C.encode("cp1251"), OWNER)["duplicates"] == 2


def test_client_bank_exchange_needs_an_opening_balance():
    text = ONE_C.replace("НачальныйОстаток=1000.00\n", "")
    with pytest.raises(ValueError, match="начального остатка"):
        parse_client_bank_exchange(text.encode("cp1251"), "ONEC")
    _, info = parse_client_bank_exchange(text.encode("cp1251"), "ONEC", opening_minor=100_000)
    assert info["closing_minor"] == 130_000


# ------------------------------------------------------------ history views and taxes


def test_recurring_and_category_summary(svc):
    found = svc.recurring("A", AS_OF)["components"]
    rhythms = {(c["category"], c["rhythm"]) for c in found}
    assert ("rent", "monthly") in rhythms
    assert any(c["rhythm"] == "weekly" and c["direction"] == "in" for c in found)
    rent = next(c for c in found if c["category"] == "rent")
    assert rent["next_dates"] == ["2026-05-15"] and rent["counterparty_inn"] == "0098765432"
    summary = svc.categories_summary("A", date(2026, 2, 1), AS_OF)
    shares = {}
    for r in summary["rows"]:
        shares[r["direction"]] = shares.get(r["direction"], 0) + r["share"]
    assert all(abs(v - 1) < 1e-3 for v in shares.values())
    assert summary["inflow_minor"] > summary["outflow_minor"] > 0


def test_tax_calendar_view_and_dictionaries(svc):
    view = svc.tax_calendar("A", AS_OF)
    assert view["rules"][0].startswith("ЕНП") and "label" in view["regime"]
    forecast = base(svc)
    applied = svc.apply_tax_calendar("A", forecast.run_id, OWNER)
    assert applied["created"] == len([e for e in view["events"] if e["in_horizon_30d"]])
    dictionaries = svc.dictionaries()
    assert len(dictionaries["categories"]) == len(Category)
    assert {s["code"] for s in dictionaries["sources"]} >= {"invoice", "contract", "bank", "ofd"}


# ------------------------------------------------------------ plan-fact, refresh, alerts, sources


def add_manual(svc, day, amount=500_000, direction="in", event_id="m1"):
    event = PlannedEvent(
        id=event_id,
        account_id="A",
        component_id="manual",
        category="customer_payment" if direction == "in" else "operating_expense",
        amount_minor=amount,
        direction=direction,
        due_date=day,
        expected_date=day,
        known_at=AS_OF,
        source="manual",
        status="confirmed",
        label="Клиент обещал оплату",
        certainty="promised",
    )
    return svc.add_event(EventCreate(event=event), OWNER)


def test_plan_fact_weeks_and_overdue_payments(svc):
    add_manual(svc, date(2026, 5, 6))
    forecast = base(svc)
    view = svc.plan_fact_view(forecast.run_id, "week")
    assert view["overdue"][0]["event_id"] == "m1" and view["overdue"][0]["days_overdue"] == 22
    assert "не поступил" in view["overdue"][0]["warning"]
    assert view["summary"]["days_compared"] == 28
    assert [w["week_start"] for w in view["weeks"]][:2] == ["2026-04-27", "2026-05-04"]
    assert all("deviation" in r for r in view["rows"])


def test_refresh_reapplies_events_without_the_model_and_detects_new_data(svc):
    forecast = base(svc)
    assert svc.refresh(forecast.run_id, OWNER)["mode"] == "unchanged"
    add_manual(svc, date(2026, 5, 12), amount=1_234_500)
    events_only = svc.refresh(forecast.run_id, OWNER)
    assert events_only["mode"] == "events_only"
    new = events_only["forecast"]
    assert (
        new["points"][-1]["closing_balance_minor"] - forecast.points[-1].closing_balance_minor
        == 1_234_500
    )
    assert new["risk"]["probability_30d"] is None and new["run_id"] != forecast.run_id
    svc.import_transactions(
        csv_bytes([transaction(operation="late", day="2026-05-29", amount="1.00", balance="1.00")]),
        OWNER,
    )
    fresh = svc.refresh(forecast.run_id, OWNER)
    assert fresh["mode"] == "new_data" and fresh["forecast"]["as_of"] == "2026-05-29"


def test_alerts_and_sources(svc):
    add_manual(svc, date(2026, 5, 4), amount=10**9, direction="out", event_id="big")
    # Expected before the forecast date and not paid: overdue on that date.
    add_manual(svc, date(2026, 4, 20), event_id="late")
    forecast = base(svc)
    alerts = svc.alerts("A", OWNER, forecast.run_id)["alerts"]
    kinds = [a["kind"] for a in alerts]
    assert kinds[0] in ("new_gap", "current_deficit")
    assert [a["action"]["event_id"] for a in alerts if a["kind"] == "overdue_payment"] == ["late"]
    svc.add_feed(
        "A",
        MarketplaceFeed(
            marketplace="Маркетплейс",
            payouts=[{"date": "2026-05-06", "amount_minor": 1}],
            known_at=AS_OF,
        ),
        OWNER,
        "marketplace",
    )
    summary = svc.sources_summary("A", OWNER)
    assert {r["source"] for r in summary["registered_events"]} == {"manual", "marketplace"}
    assert [s["provider"] for s in summary["syncs"]] == ["marketplace"]
    assert summary["statement"]["operations"] > 0


def test_counterparty_registry_offline_and_demo(svc, tmp_path, monkeypatch):
    assert inn_checksum_valid("7707083893") and not inn_checksum_valid("0012345678")
    demo = tmp_path / "registry.json"
    demo.write_text(
        '{"0012345678": {"status": "liquidating", "name": "Клиент", "note": "демо"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CASHFLOW_REGISTRY_DEMO", str(demo))
    monkeypatch.delenv("DADATA_API_KEY", raising=False)
    assert CounterpartyRegistry().check("7707083893")["note"].startswith("Реестр не подключён")
    risks = svc.counterparty_risks("A", AS_OF, OWNER)
    flagged = [r for r in risks["counterparties"] if r["check"]["risk"] == "critical"]
    assert flagged and flagged[0]["suggested_scenario"]["kind"] == "client_delay"
    assert flagged[0]["check"]["source"] == "demo_registry"


def test_new_endpoints_require_access(client, auth):
    bob = {"Authorization": "Bearer bob-token"}
    for path in (
        "/api/v1/accounts/A/recurring?as_of=2026-04-30",
        "/api/v1/accounts/A/sources",
        "/api/v1/accounts/A/alerts",
        "/api/v1/accounts/A/tax-calendar?as_of=2026-04-30",
    ):
        assert client.get(path, headers=bob).status_code == 403
        assert client.get(path, headers=auth).status_code == 200, path
    parsed = client.post(
        "/api/v1/events/parse",
        headers=auth,
        json={"text": "Клиент обещал заплатить 300 000 рублей 20-го", "today": "2026-04-26"},
    )
    assert parsed.json()["draft"]["amount_minor"] == 30_000_000
    assert client.get("/api/v1/dictionaries", headers=auth).status_code == 200
    contract = client.post(
        "/api/v1/accounts/A/contracts",
        headers=bob,
        json={
            "contract_id": "1",
            "direction": "in",
            "total_minor": 100,
            "signed_date": "2026-04-30",
            "prepayment_pct": 100,
        },
    )
    assert contract.status_code == 403
    plan = client.get("/api/v1/plan-fact", headers=auth, params={"run_id": "missing"})
    assert plan.status_code == 422


def test_dashboard_calendar_shows_new_sources(svc):
    from apps.dashboard.calendar import calendar_view

    forecast = base(svc)
    feed = MarketplaceFeed(
        marketplace="Маркетплейс",
        payouts=[{"date": "2026-05-06", "amount_minor": 1000}],
        known_at=AS_OF,
    )
    event = svc.add_feed("A", feed, OWNER, "marketplace")["events"][0]
    payload = forecast.model_dump(mode="json")
    rows, _ = calendar_view(payload, [event], [event], "A", str(AS_OF))
    assert any(r["source"].startswith("Маркетплейс") for r in rows)
