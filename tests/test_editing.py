"""Editing the register (edit, cancel, correct an expectation) and the filtered calendar and
operations views."""

from datetime import date

import pytest
from conftest import OWNER

from cashflow_domain.feeds import AssumptionEdit, EventEdit
from cashflow_domain.schemas import EventCreate, ForecastRequest, PaymentLink, PlannedEvent

AS_OF = date(2026, 4, 30)


def base(svc, as_of=AS_OF):
    return svc.forecast(ForecastRequest(account_id="A", as_of=as_of, model="weekday"))


def manual(svc, event_id, day, amount, direction="out", **kw):
    event = PlannedEvent(
        id=event_id,
        account_id="A",
        component_id="manual",
        category="operating_expense" if direction == "out" else "customer_payment",
        amount_minor=amount,
        direction=direction,
        due_date=day,
        expected_date=day,
        known_at=kw.pop("known_at", AS_OF),
        source="manual",
        status="confirmed",
        label=kw.pop("label", "Оплата подрядчику"),
        **kw,
    )
    return svc.add_event(EventCreate(event=event), OWNER)


def minimum(forecast):
    return forecast.risk.minimum_balance_minor


def outflow(forecast):
    return sum(p.event_out_minor for p in forecast.points)


def paid_out(svc):
    return next(t for t in reversed(svc.store.transactions("A", AS_OF)) if t["direction"] == "out")[
        "operation_id"
    ]


def regular(forecast, direction="out"):
    return next(e for e in forecast.events if e.source == "regular" and e.direction == direction)


# ------------------------------------------------------------ registered events


def test_edit_writes_a_revision_seen_only_from_its_date(svc):
    manual(svc, "fix", date(2026, 5, 12), 1_000_000, known_at=date(2026, 4, 20))
    edited = svc.edit_event(
        "fix",
        EventEdit(known_at=AS_OF, amount_minor=400_000, expected_date=date(2026, 5, 20)),
        OWNER,
    )
    assert (edited.amount_minor, edited.expected_date) == (400_000, date(2026, 5, 20))
    assert edited.label == "Оплата подрядчику"
    before = {e.id: e for e in svc.events("A", date(2026, 4, 25))}
    after = {e.id: e for e in svc.events("A", AS_OF)}
    assert before["fix"].amount_minor == 1_000_000
    assert after["fix"].amount_minor == 400_000
    # The same values again are not a new version.
    version = svc.store.account("A")["event_version"]
    svc.edit_event("fix", EventEdit(known_at=AS_OF, amount_minor=400_000), OWNER)
    assert svc.store.account("A")["event_version"] == version


def test_edit_keeps_the_order_of_versions_and_the_paid_part(svc):
    manual(svc, "fix", date(2026, 5, 12), 1_000_000)
    with pytest.raises(ValueError, match="порядок версий"):
        svc.edit_event("fix", EventEdit(known_at=date(2026, 4, 1), amount_minor=5), OWNER)
    operation = paid_out(svc)
    svc.link_payment(
        "fix", PaymentLink(operation_id=operation, amount_minor=300_000, known_at=AS_OF), OWNER
    )
    with pytest.raises(ValueError, match="оплаченной"):
        svc.edit_event("fix", EventEdit(known_at=AS_OF, amount_minor=200_000), OWNER)


def test_cancelled_event_leaves_the_forecast_and_fast_refresh_agrees(svc):
    first = base(svc)
    manual(svc, "fix", date(2026, 5, 12), 5_000_000)
    with_event = base(svc)
    assert minimum(with_event) < minimum(first)
    cancelled = svc.cancel_event("fix", AS_OF, OWNER)
    assert cancelled.status == "cancelled"
    assert svc.cancel_event("fix", AS_OF, OWNER) == cancelled
    refreshed = svc.refresh(with_event.run_id, OWNER)
    assert refreshed["mode"] == "events_only"
    assert refreshed["forecast"]["risk"]["minimum_balance_minor"] == minimum(first)
    assert minimum(base(svc)) == minimum(first)
    with pytest.raises(ValueError, match="отменено"):
        svc.edit_event("fix", EventEdit(known_at=AS_OF, amount_minor=1), OWNER)


def test_partly_paid_event_stays_cancelled(svc):
    manual(svc, "fix", date(2026, 5, 12), 1_000_000)
    operation = paid_out(svc)
    svc.link_payment(
        "fix", PaymentLink(operation_id=operation, amount_minor=300_000, known_at=AS_OF), OWNER
    )
    svc.cancel_event("fix", AS_OF, OWNER)
    assert {e.id: e.status for e in svc.events("A", AS_OF)}["fix"] == "cancelled"


# ------------------------------------------------------------ history-based expectations


def test_expectation_is_replaced_by_the_users_version(svc):
    first = base(svc)
    rent = regular(first)
    event = svc.edit_assumption(
        first.run_id,
        rent.id,
        AssumptionEdit(action="edit", known_at=AS_OF, amount_minor=rent.amount_minor * 2),
        OWNER,
    )
    assert event.decision == "replace" and event.replacement_id == rent.id
    second = base(svc)
    ids = {e.id for e in second.events}
    assert rent.id not in ids and event.id in ids
    assert outflow(second) == outflow(first) + rent.amount_minor
    with pytest.raises(ValueError, match="уже исправлено"):
        svc.edit_assumption(
            first.run_id, rent.id, AssumptionEdit(action="skip", known_at=AS_OF), OWNER
        )


def test_skipped_expectation_leaves_the_forecast(svc):
    first = base(svc)
    rent = regular(first)
    svc.edit_assumption(first.run_id, rent.id, AssumptionEdit(action="skip", known_at=AS_OF), OWNER)
    second = base(svc)
    assert rent.id not in {e.id for e in second.events}
    assert (
        sum(p.event_out_minor for p in second.points)
        == sum(p.event_out_minor for p in first.points) - rent.amount_minor
    )
    refreshed = svc.refresh(first.run_id, OWNER)["forecast"]
    assert refreshed["risk"]["minimum_balance_minor"] == second.risk.minimum_balance_minor
    # The constant model has no expectations to replace, a skipped one does not block it.
    svc.forecast(ForecastRequest(account_id="A", as_of=AS_OF, model="constant"))


def test_unknown_expectation_is_rejected(svc):
    first = base(svc)
    with pytest.raises(ValueError, match="не найдено"):
        svc.edit_assumption(
            first.run_id, "regular:A:none", AssumptionEdit(action="skip", known_at=AS_OF), OWNER
        )


# ------------------------------------------------------------ payment calendar


def test_calendar_lists_actions_filters_and_totals(svc):
    manual(svc, "fix", date(2026, 5, 12), 700_000, label="Ремонт склада")
    manual(svc, "promise", date(2026, 5, 6), 300_000, direction="in", label="Обещал клиент")
    forecast = base(svc)
    view = svc.calendar_view(forecast.run_id, OWNER, {})
    items = {i["id"]: i for i in view["items"]}
    assert items["fix"]["actions"] == ["edit", "delete"]
    assert items["fix"]["kind"] == "registered" and items["fix"]["source_label"]
    rent = regular(forecast)
    assert items[rent.id]["actions"] == ["edit", "skip"] and items[rent.id]["obligatory"]
    dates = [i["date"] for i in view["items"]]
    assert dates == sorted(dates)
    assert view["totals"]["count"] == len(view["items"])
    assert view["totals"]["inflow_minor"] == sum(
        i["amount_minor"] for i in view["items"] if i["direction"] == "in"
    )
    assert {f["code"] for f in view["facets"]["sources"]} >= {"manual", "regular"}
    assert view["facets"]["obligatory"] == sum(i["obligatory"] for i in view["items"])
    assert items[rent.id]["title"] == items[rent.id]["category_label"]
    assert items[rent.id]["component_id"] == rent.component_id

    only = svc.calendar_view(forecast.run_id, OWNER, {"direction": ["in"], "q": "обещал"})
    assert [i["id"] for i in only["items"]] == ["promise"]
    mandatory = svc.calendar_view(forecast.run_id, OWNER, {"obligatory": True})
    assert all(i["obligatory"] for i in mandatory["items"]) and "fix" not in {
        i["id"] for i in mandatory["items"]
    }
    window = svc.calendar_view(
        forecast.run_id, OWNER, {"date_from": date(2026, 5, 10), "date_to": date(2026, 5, 13)}
    )
    assert all(date(2026, 5, 10) <= i["date"] <= date(2026, 5, 13) for i in window["items"])

    svc.cancel_event("fix", AS_OF, OWNER)
    after = svc.calendar_view(svc.refresh(forecast.run_id, OWNER)["forecast"]["run_id"], OWNER, {})
    assert "fix" not in {i["id"] for i in after["items"]} and after["facets"]["cancelled"] == 1
    shown = svc.calendar_view(after["run_id"], OWNER, {"include_cancelled": True})
    assert {i["id"]: i["actions"] for i in shown["items"]}["fix"] == []
    # A shown deleted payment stays out of the totals, the count included.
    assert shown["totals"] == after["totals"]


# ------------------------------------------------------------ operations


def test_operations_filters_totals_and_facets(svc):
    everything = svc.transactions_view("A", OWNER, {}, 1, 50)
    cut = svc.transactions_view("A", OWNER, {"date_to": AS_OF}, 1, 500)
    assert cut["total"] < everything["total"]
    assert all(str(t["operation_date"]) <= str(AS_OF) for t in cut["items"])
    rent = svc.transactions_view(
        "A", OWNER, {"date_to": AS_OF, "q": "аренда", "direction": "out"}, 1, 50
    )
    assert rent["total"] and all(t["direction"] == "out" for t in rent["items"])
    assert rent["totals"]["outflow_minor"] == 800_003 * rent["total"]
    assert rent["totals"]["inflow_minor"] == 0
    biggest = svc.transactions_view("A", OWNER, {"date_to": AS_OF, "sort": "amount_desc"}, 1, 3)
    amounts = [t["amount_minor"] for t in biggest["items"]]
    assert amounts == sorted(amounts, reverse=True) and len(amounts) == 3
    assert {"categories", "counterparties"} <= set(cut["facets"])
    assert (
        cut["facets"]["counterparties"][0]["count"] >= cut["facets"]["counterparties"][-1]["count"]
    )
    with pytest.raises(ValueError, match="позже"):
        svc.transactions_view("A", OWNER, {"date_from": AS_OF, "date_to": date(2026, 1, 1)}, 1, 5)


def test_structure_groups_add_up_and_follow_the_filters(svc):
    summary = svc.categories_summary("A", date(2026, 1, 1), AS_OF)
    for direction in ("in", "out"):
        shares = [g["share"] for g in summary["groups"] if g["direction"] == direction]
        assert abs(sum(shares) - 1) < 0.01
    outflow = svc.categories_summary(
        "A", date(2026, 1, 1), AS_OF, {"direction": "out", "q": "аренда"}
    )
    assert outflow["inflow_minor"] == 0 and outflow["outflow_minor"] > 0
    assert (
        outflow["outflow_minor"]
        == svc.transactions_view(
            "A",
            OWNER,
            {"date_from": date(2026, 1, 1), "date_to": AS_OF, "direction": "out", "q": "аренда"},
            1,
            5,
        )["totals"]["outflow_minor"]
    )


# ------------------------------------------------------------ HTTP contract


def test_editing_endpoints_match_their_response_models(client, auth):
    forecast = client.post(
        "/api/v1/forecasts",
        json={"account_id": "A", "as_of": str(AS_OF), "model": "weekday"},
        headers=auth,
    ).json()
    run_id = forecast["run_id"]
    event = {
        "id": "api-fix",
        "account_id": "A",
        "component_id": "manual",
        "category": "operating_expense",
        "amount_minor": 100_000,
        "direction": "out",
        "due_date": "2026-05-12",
        "expected_date": "2026-05-12",
        "known_at": str(AS_OF),
        "source": "manual",
        "status": "confirmed",
    }
    assert client.post("/api/v1/events", json={"event": event}, headers=auth).status_code == 200
    edited = client.patch(
        "/api/v1/events/api-fix",
        json={"known_at": str(AS_OF), "amount_minor": 150_000, "label": "Ремонт"},
        headers=auth,
    )
    assert edited.status_code == 200 and edited.json()["amount_minor"] == 150_000
    moved = client.patch(
        "/api/v1/events/api-fix",
        json={"known_at": str(AS_OF), "expected_date": "2026-05-14"},
        headers=auth,
    )
    assert moved.json()["expected_date"] == "2026-05-14"
    calendar = client.get(
        f"/api/v1/forecasts/{run_id}/calendar",
        params={"direction": ["out"], "obligatory": "true"},
        headers=auth,
    )
    assert calendar.status_code == 200, calendar.text
    expectation = next(i for i in calendar.json()["items"] if i["kind"] == "assumption")
    skipped = client.post(
        f"/api/v1/forecasts/{run_id}/assumptions/{expectation['id']}",
        json={"action": "skip", "known_at": str(AS_OF)},
        headers=auth,
    )
    assert skipped.status_code == 200 and skipped.json()["status"] == "cancelled"
    gone = client.delete("/api/v1/events/api-fix", params={"known_at": str(AS_OF)}, headers=auth)
    assert gone.status_code == 200 and gone.json()["status"] == "cancelled"
    assert client.delete("/api/v1/events/nope", params={"known_at": str(AS_OF)}, headers=auth)
    operations = client.get(
        "/api/v1/accounts/A/transactions",
        params={"date_to": str(AS_OF), "category": ["rent"], "sort": "amount_asc"},
        headers=auth,
    )
    assert operations.status_code == 200, operations.text
    summary = client.get(
        "/api/v1/accounts/A/categories/summary",
        params={"date_to": str(AS_OF), "direction": "out"},
        headers=auth,
    )
    assert summary.status_code == 200 and summary.json()["groups"]
    other = {"Authorization": "Bearer bob-token"}
    assert (
        client.patch(
            "/api/v1/events/api-fix", json={"known_at": str(AS_OF)}, headers=other
        ).status_code
        == 403
    )
