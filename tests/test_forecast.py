from datetime import date, timedelta

import numpy as np
import pytest
from conftest import OWNER, csv_bytes, transaction

from cashflow_domain.schemas import ForecastRequest
from cashflow_ml.features import feature_matrix, flow_frame, prepare
from cashflow_ml.models import predict, train_model, training_origins
from cashflow_ml.regularity import component_key, detect_components


def test_30_calendar_points_and_balance_invariant(svc):
    result = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    )
    assert [p.date for p in result.points] == [
        date(2026, 5, 1) + timedelta(days=i) for i in range(30)
    ]
    balance = result.opening_balance_minor
    for p in result.points:
        balance += p.event_in_minor - p.event_out_minor + p.residual_in_minor - p.residual_out_minor
        assert balance == p.closing_balance_minor
    assert result.risk.probability_30d is None
    assert result.risk.calibration_status == "unavailable"


def test_future_data_never_changes_past_features_or_forecast(svc):
    as_of = date(2026, 4, 30)
    txs = svc.store.transactions("A")
    before = svc.forecast(ForecastRequest(account_id="A", as_of=as_of, model="structural"))
    f, _, scale = prepare(txs, as_of, True)
    matrix = feature_matrix(f, as_of, scale)
    svc.import_transactions(
        csv_bytes(
            [
                transaction(
                    operation="future-large",
                    day="2026-06-01",
                    amount="9999999.99",
                    balance="9999999.99",
                )
            ]
        ),
        OWNER,
    )
    after = svc.forecast(ForecastRequest(account_id="A", as_of=as_of, model="structural"))
    f2, _, scale2 = prepare(svc.store.transactions("A"), as_of, True)
    np.testing.assert_array_equal(matrix, feature_matrix(f2, as_of, scale2))
    assert before.points == after.points
    assert before.events == after.events


def test_actual_hgb_training_bounds_and_past_model_selection(svc):
    cutoff = date(2026, 3, 31)
    data = {"A": (svc.store.transactions("A"), svc.store.daily("A"))}
    meta = train_model(
        data,
        cutoff,
        True,
        svc.registry,
        "fixture",
        {"hgb": {"max_iter": 8, "max_leaf_nodes": 5, "min_samples_leaf": 10}},
    )
    assert date.fromisoformat(meta["max_target_date"]) <= cutoff
    assert date.fromisoformat(meta["max_origin"]) + timedelta(days=30) <= cutoff
    with pytest.raises(ValueError, match="Нет обученной"):
        svc.registry.select(cutoff - timedelta(days=1), True)
    old = predict(data["A"][0], date(2026, 4, 30), "hgb", True, svc.registry)[0]
    changed = [dict(t) for t in data["A"][0]]
    for tx in changed:
        if tx["operation_date"] > "2026-04-30":
            tx["amount_minor"] = 10**12
            tx["payment_purpose"] = "future forbidden"
    new = predict(changed, date(2026, 4, 30), "hgb", True, svc.registry)[0]
    np.testing.assert_array_equal(old, new)


def test_regular_components_have_exactly_one_owner(svc):
    txs = svc.store.transactions("A", date(2026, 4, 30))
    comps = detect_components(txs, date(2026, 4, 30))
    assert comps
    residual = flow_frame(txs, date(2025, 10, 1), date(2026, 4, 30), comps)
    owned = sum(t["amount_minor"] for t in txs if component_key(t) in comps)
    assert residual.to_numpy().sum() + owned == sum(t["amount_minor"] for t in txs)


def test_training_origins_require_full_horizon():
    cutoff = date(2026, 1, 31)
    origins = list(training_origins(date(2025, 1, 1), cutoff))
    assert origins and all(o + timedelta(days=30) <= cutoff for o in origins)


def test_explicit_model_version_is_checked(svc):
    request = ForecastRequest(
        account_id="A", as_of=date(2026, 4, 30), model="weekday", model_version="weekday-v1"
    )
    assert svc.forecast(request).model_version == "weekday-v1"
    with pytest.raises(ValueError, match="версия модели"):
        svc.forecast(request.model_copy(update={"model_version": "missing-version"}))


def test_short_history_honest_probability_and_no_future_extrapolated_asof(tmp_path):
    from cashflow_services.service import Service

    svc = Service(tmp_path / "db", tmp_path / "artifacts")
    svc.import_transactions(csv_bytes([transaction()]), OWNER)
    result = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 1, 1), model="structural")
    )
    assert result.risk.probability_30d is None
    assert any("Короткая история" in flag for flag in result.quality_flags)
    with pytest.raises(ValueError, match="за пределами"):
        svc.forecast(ForecastRequest(account_id="A", as_of=date(2026, 1, 2), model="structural"))
