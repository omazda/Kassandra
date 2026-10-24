from datetime import date, timedelta

import numpy as np
from conftest import OWNER, csv_bytes, transaction

from cashflow_domain.schemas import ASSUMPTION_SOURCES, EventCreate, ForecastRequest, PlannedEvent
from cashflow_ml import calendar_ru as cal
from cashflow_ml import structural as S
from cashflow_ml.mechanisms import detect_mechanisms

AS_OF = date(2026, 2, 1)
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


def invoice_payment(doc_date, lag, amount):
    paid = cal.next_working_day(doc_date + timedelta(days=lag))
    purpose = f"Оплата по счету № {doc_date:%j} от {doc_date:%d.%m.%Y}"
    return tx(paid, amount, "customer_payment", "in", "222", "Клиент", purpose)


def history(with_future=True):
    """Аренда по 10-м числам, документы клиента каждые 3 дня с оплатой через 10 дней, прочее."""
    out = []
    for y, m in [(2025, k) for k in range(2, 13)] + [(2026, 1), (2026, 2)]:
        out.append(tx(cal.nominal_date(y, m, 10, "next"), 5_000_00, "rent", inn="333"))
    day = date(2025, 6, 1)
    while day <= AS_OF:
        out.append(invoice_payment(day, 10, 100_000_00 + day.day))
        day += timedelta(days=3)
    day = date(2025, 6, 2)
    while day <= AS_OF + timedelta(days=30):
        if cal.is_working_day(day) and day.weekday() == 2:
            out.append(tx(day, 2_000_00, "operating_expense", inn="444", purpose="Хозрасходы"))
        day += timedelta(days=1)
    out.append(tx(date(2026, 1, 20), 9_000_00, "owner_funding", "in", "555"))
    out.sort(key=lambda t: t["operation_date"])
    if not with_future:
        out = [t for t in out if t["operation_date"] <= AS_OF.isoformat()]
    return out


def setup_universe(all_txs):
    S.configure(S.build_universe({"A": (all_txs, [])}), {"A": {"legal_form": "ООО"}})


def past(txs):
    return [t for t in txs if t["operation_date"] <= AS_OF.isoformat()]


def teardown_function():
    S.configure(None, {})


def test_every_history_operation_has_one_owner():
    txs = history()
    h = past(txs)
    model = detect_mechanisms(h, AS_OF)
    lags = S.doc_lags(h, AS_OF, AS_OF - timedelta(days=89))
    owners = [S.owner_of(t, model, lags) for t in h]
    assert set(owners) <= {"financing", "mechanism", "documents", "residual"}
    rent = [S.owner_of(t, model, lags) for t in h if t["category"] == "rent"]
    assert rent and set(rent) == {"mechanism"}
    assert {S.owner_of(t, model, lags) for t in h if t["category"] == "owner_funding"} == {
        "financing"
    }
    docs = [
        t for t in h if t["category"] == "customer_payment" and t["operation_date"] >= "2025-11"
    ]
    assert {S.owner_of(t, model, lags) for t in docs} == {"documents"}
    # дублей между владельцами нет: механизмы и документы не пересекаются по операциям
    assert not any(model.owns(t) and t["operation_id"] in lags for t in h)


def test_residual_excludes_owned_operations_without_double_count():
    txs = history()
    h = past(txs)
    model = detect_mechanisms(h, AS_OF)
    lags = S.doc_lags(h, AS_OF, AS_OF - timedelta(days=89))
    with_docs = S.residual_profile(h, AS_OF, model, lags)
    without = S.residual_profile(h, AS_OF, model, None)
    # аренда (механизм) в остатке отсутствует: остаток расходов - только хозрасходы 2 000 руб
    assert with_docs.values[:, 2].max() <= 2_000_00 + 1
    # лаг оплаты 10 дней: на днях 1..10 поток клиента принадлежит реестру, в остатке его нет
    assert with_docs.values[:10, 0].sum() == 0 < without.values[:10, 0].sum()
    # после лага документ ещё не выставлен на as_of, поток возвращается в остаток целиком
    assert np.allclose(with_docs.values[20:], without.values[20:])
    assert with_docs.owned["documents"] > 0 and with_docs.owned["mechanism"] > 0


def test_events_known_at_and_sources():
    txs = history()
    setup_universe(txs)
    result = S.structural_forecast(past(txs), AS_OF, "A", tax=True, docs=True, alpha=0.0)
    assert result.events
    assert all(e.known_at <= AS_OF for e in result.events)
    assert all(AS_OF < e.expected_date <= AS_OF + timedelta(days=30) for e in result.events)
    assert {e.source for e in result.events} <= ASSUMPTION_SOURCES
    docs = [e for e in result.events if e.source == "documents"]
    regular = [e for e in result.events if e.source != "documents"]
    assert docs and regular
    # документ известен с даты документа, не позже as_of
    assert all(e.known_at < AS_OF and e.document_ref for e in docs)
    assert all(e.category == "customer_payment" for e in docs)
    # одна категория - один владелец: у документов и механизмов нет общих (категория, направление)
    assert {(e.category, e.direction) for e in docs}.isdisjoint(
        {(e.category, e.direction) for e in regular}
    )
    ids = [e.id for e in result.events]
    assert len(ids) == len(set(ids))


def test_registry_contains_only_documents_issued_by_as_of():
    txs = history()
    # документ после as_of, оплаченный в будущем: на as_of его существование неизвестно
    late_doc = AS_OF + timedelta(days=2)
    txs.append(invoice_payment(late_doc, 5, 777_000_00))
    setup_universe(txs)
    result = S.structural_forecast(past(txs), AS_OF, "A", docs=True, alpha=0.0)
    assert all(e.amount_minor != 777_000_00 for e in result.events)
    assert all(e.known_at <= AS_OF for e in result.events)


def test_forecast_ignores_operations_after_as_of_without_registry():
    txs = history()
    S.configure(None, {})
    base = S.structural_forecast(past(txs), AS_OF, "A", docs=True, alpha=0.5)
    noisy = past(txs) + [tx(AS_OF + timedelta(days=3), 10**9, "customer_payment", "in")]
    again = S.structural_forecast(noisy, AS_OF, "A", docs=True, alpha=0.5)
    assert np.array_equal(base.values, again.values)
    assert [e.id for e in base.events] == [e.id for e in again.events]
    assert not base.meta["documents_registry"]


def test_registry_unavailable_at_archive_end_keeps_documents_in_residual():
    txs = past(history())  # в архиве нет оплат после as_of: реестр ничего не знает
    setup_universe(txs)
    on = S.structural_forecast(txs, AS_OF, "A", docs=True, alpha=0.0)
    off = S.structural_forecast(txs, AS_OF, "A", docs=False, alpha=0.0)
    assert not on.meta["documents_registry"]
    assert np.array_equal(on.values, off.values)


def test_registry_replaces_residual_flow_not_adds():
    """Сумма на дне 1..10 даётся документами, а не остатком: итог не больше прежнего потока."""
    txs = history()
    setup_universe(txs)
    on = S.structural_forecast(past(txs), AS_OF, "A", tax=False, docs=True, alpha=0.0)
    off = S.structural_forecast(past(txs), AS_OF, "A", tax=False, docs=False, alpha=0.0)
    inflow_on = (
        sum(e.amount_minor for e in on.events if e.direction == "in") + on.values[:, 0].sum()
    )
    inflow_off = off.values[:, 0].sum() + sum(
        e.amount_minor for e in off.events if e.direction == "in"
    )
    # поток клиентов в 30 днях в обоих вариантах одного порядка; нет удвоения
    assert inflow_on < 1.35 * inflow_off


def test_trend_is_damped_and_zero_alpha_is_identity():
    values = np.zeros((30, 3))
    assert np.array_equal(S.apply_trend(values, 1000.0, 0.0), values)
    up = S.apply_trend(values, 1000.0, 1.0)
    assert up[0, 0] > up[29, 0] > 0 and up[:, 2].sum() == 0
    down = S.apply_trend(values, -1000.0, 1.0)
    assert down[0, 2] > down[29, 2] > 0 and down[:, 0].sum() == 0


def test_balances_use_events_and_residual_via_assemble():
    txs = history()
    setup_universe(txs)
    result = S.structural_forecast(past(txs), AS_OF, "A", docs=True, alpha=0.0)
    balances = S.balances_of(result, AS_OF, 1_000_000_00)
    assert len(balances) == 30
    delta = balances[-1] - 1_000_000_00
    net_events = sum(
        e.amount_minor if e.direction == "in" else -e.amount_minor for e in result.events
    )
    v = np.maximum(0, result.values)
    # округление остатка по дням допускает расхождение до 30 копеек
    assert abs(delta - (net_events + v[:, 0].sum() + v[:, 1].sum() - v[:, 2].sum())) <= 30


def test_service_structural_forecast_events_and_replacement(svc):
    request = ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    assert request.model == "structural"
    before = svc.forecast(request)
    assert before.model_version.startswith("structural-v1")
    assert before.trained_to is None
    assert all(e.known_at <= before.as_of for e in before.events)
    rent = next(e for e in before.events if e.category == "rent")
    assert rent.source in ASSUMPTION_SOURCES
    replacement = PlannedEvent(
        id="rent-confirmed",
        account_id="A",
        component_id=rent.component_id,
        category="rent",
        amount_minor=rent.amount_minor,
        direction="out",
        due_date=rent.due_date,
        expected_date=rent.expected_date,
        known_at=before.as_of,
        source="manual",
        status="confirmed",
        replacement_id=rent.id,
        decision="replace",
    )
    svc.add_event(EventCreate(event=replacement, base_run_id=before.run_id), OWNER)
    after = svc.forecast(request)
    assert rent.id not in {e.id for e in after.events}
    assert before.points == after.points  # подтверждённое событие заместило предположение
    assert sum(e.component_id == rent.component_id for e in after.events) == 1


def test_models_predict_structural_requires_account(svc):
    import pytest

    from cashflow_ml.models import predict

    with pytest.raises(ValueError):
        predict(
            svc.store.transactions("A", date(2026, 4, 30)),
            date(2026, 4, 30),
            "structural",
            True,
            svc.registry,
        )


def test_product_exposes_confidence_and_wires_cold_start(svc, tmp_path):
    from cashflow_ml import cold_start

    full = svc.forecast(ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="mean"))
    assert full.confidence["level"] in ("medium", "high") and not full.confidence["refuse"]
    from cashflow_services.service import Service

    tiny = Service(tmp_path / "db", tmp_path / "artifacts")
    tiny.import_transactions(csv_bytes([transaction()]), OWNER)
    short = tiny.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 1, 1), model="structural")
    )
    assert short.confidence["level"] in ("none", "very_low")
    assert any("Уровень доверия" in f for f in short.quality_flags)
    assert cold_start.available()  # реестр доноров подключён сервисом


def test_structural_does_not_use_retro_registry_unless_requested(svc):
    base = ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    honest = svc.forecast(base)
    assert not any("БЭКТЕСТ С УТЕЧКОЙ" in f for f in honest.quality_flags)
    assert all(e.source != "documents" for e in honest.events)
    assert ForecastRequest(account_id="A", as_of=date(2026, 4, 30)).model == "hgb"
