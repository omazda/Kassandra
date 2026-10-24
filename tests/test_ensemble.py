import json
from datetime import date, timedelta

import numpy as np
import pytest

from cashflow_domain.engine import assemble, deterministic_risk
from cashflow_domain.schemas import ForecastRequest, PlannedEvent
from cashflow_ml import ensemble as E
from cashflow_ml import protocol_v3 as p3
from cashflow_ml.models import train_model
from cashflow_ml.risk import add_uncertainty

SPEC = [("hgb_v2", "all"), ("hgb_cal", "all"), ("shape", "all")]


def model_for(theta, available_from="2026-03-31", spec=SPEC):
    return E.make_model(
        spec, theta, 0.0, {"fit_to": available_from, "available_from": available_from}
    )


def test_feature_paths_split_buckets_and_parts():
    delta = np.arange(1, 31, dtype=float) * 10 + np.where(np.arange(30) % 7 == 0, -50, 0)
    x = E.feature_paths({"a": delta}, [("a", "all")])
    assert x.shape == (4, 30)
    np.testing.assert_allclose(x.sum(0), delta)
    assert np.all(x[0, 7:] == 0) and np.all(x[3, :21] == 0)
    shape, drift = E.feature_paths({"a": delta}, [("a", "shape"), ("a", "drift")]).reshape(2, 4, 30)
    np.testing.assert_allclose(shape.sum(0) + drift.sum(0), delta)
    assert drift.sum(0)[-1] == pytest.approx(delta[-1])
    assert shape.sum(0)[-1] == pytest.approx(0)


def test_combine_damps_toward_constant():
    deltas = {"hgb_v2": np.full(30, 1000.0), "hgb_cal": np.zeros(30), "shape": np.zeros(30)}
    zero = model_for([0.0] * 12)
    assert np.all(E.combine(deltas, zero) == 0)
    half = model_for([0.5] * 4 + [0.0] * 8)
    assert np.all(E.combine(deltas, half) == 500)
    bucket = model_for([1.0, 0.0, 0.0, 0.25] + [0.0] * 8)
    path = E.combine(deltas, bucket)
    assert np.all(path[:7] == 1000) and np.all(path[7:21] == 0) and np.all(path[21:] == 250)


def test_residual_flows_reproduce_path_with_events_exactly():
    as_of = date(2026, 4, 30)
    rng = np.random.default_rng(1)
    path = np.cumsum(rng.integers(-50_000, 50_000, 30))
    carrier = rng.uniform(0, 30_000, (30, 3))
    salary = PlannedEvent(
        id="regular:A:salary:2026-05-10",
        account_id="A",
        component_id="salary",
        category="salary",
        amount_minor=400_000,
        direction="out",
        due_date=date(2026, 5, 10),
        expected_date=date(2026, 5, 10),
        known_at=as_of,
        source="regular",
        status="assumption",
    )
    values = E.residual_flows(path, [salary], carrier, as_of)
    assert np.all(values >= 0)
    closing = [p.closing_balance_minor for p in assemble(as_of, 1_000_000, values, [salary])]
    np.testing.assert_array_equal(closing, 1_000_000 + path)
    # The carrier's gross flows are kept; only the day's mismatch is added to one side.
    assert np.all(values[:, 0] == np.rint(carrier[:, 0]))


def test_fit_theta_recovers_shrinkage_and_respects_bounds():
    rng = np.random.default_rng(7)
    n = 300
    signal = rng.normal(0, 1000, (n, 30)).cumsum(1)
    noise = rng.normal(0, 300, (n, 30)).cumsum(1)
    y = 0.4 * signal + noise
    # An unrelated component (its own random walk) must get almost no weight.
    useless = rng.normal(0, 1000, (n, 30)).cumsum(1)
    x = np.stack(
        [
            np.vstack([E.feature_paths({"s": s, "u": u}, [("s", "all"), ("u", "all")])])
            for s, u in zip(signal, useless, strict=True)
        ]
    )
    groups = np.repeat(np.arange(30), 10)
    wt, wm = E.company_weights(groups, np.zeros(n), y)
    theta = E.fit_theta(x, y, y.min(1), wt, wm)
    assert np.all((theta >= 0) & (theta <= 1))
    np.testing.assert_allclose(theta[:4], 0.4, atol=0.08)
    assert np.all(theta[4:] < 0.05)


def test_company_weights_equalise_companies():
    groups = np.asarray(["a", "a", "b"])
    opening = np.zeros(3)
    target = np.vstack([np.full(30, 10.0), np.full(30, 30.0), np.full(30, 100.0)])
    wt, wm = E.company_weights(groups, opening, target)
    assert wt[0] == wt[1] == pytest.approx(1 / 40)
    assert wt[2] == pytest.approx(1 / 100)
    assert wm[2] == pytest.approx(1 / 100)


def _trained(svc):
    data = {"A": (svc.store.transactions("A"), svc.store.daily("A"))}
    params = {"hgb": {"max_iter": 8, "max_leaf_nodes": 5, "min_samples_leaf": 10}}
    for calendar in (False, True):
        train_model(data, date(2026, 3, 31), calendar, svc.registry, "fixture", params)
    return data


def _write_weights(svc, model):
    path = svc.artifacts / E.WEIGHTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model))


def test_product_ensemble_matches_protocol_and_ignores_future(svc):
    data = _trained(svc)
    model = model_for([0.3, 0.2, 0.1, 0.1, 0.1, 0.2, 0.0, 0.0, 0.0, 0.3, 0.4, 0.5])
    _write_weights(svc, model)
    as_of = date(2026, 4, 30)
    result = svc.forecast(ForecastRequest(account_id="A", as_of=as_of, model="ensemble"))
    assert result.model == "ensemble" and result.model_version == model["version"]
    balances = [p.closing_balance_minor for p in result.points]
    previous = result.opening_balance_minor
    for p in result.points:
        assert p.closing_balance_minor == previous + p.event_in_minor - p.event_out_minor + (
            p.residual_in_minor - p.residual_out_minor
        )
        previous = p.closing_balance_minor
    txs, daily = data["A"]
    past = p3.categories_at([t for t in txs if t["operation_date"] <= str(as_of)], as_of)
    visible = [d for d in daily if d["date"] <= str(as_of)]
    ctx = p3.Context("A", as_of, result.opening_balance_minor, svc.registry, {}).bind(past, visible)
    forecast = E.protocol_method(model)(past, visible, as_of, ctx)
    assert forecast.balances_minor == balances
    assert balances == [result.opening_balance_minor + int(v) for v in E.combine(
        E.component_deltas(ctx), model
    )]  # fmt: skip
    # Future operations cannot change the forecast for a past date.
    changed = [dict(t) for t in txs]
    for tx in changed:
        if tx["operation_date"] > str(as_of):
            tx["amount_minor"] = 10**12
    ctx2 = p3.Context("A", as_of, 0, svc.registry, {}).bind(
        p3.categories_at([t for t in changed if t["operation_date"] <= str(as_of)], as_of), visible
    )
    np.testing.assert_array_equal(
        E.component_deltas(ctx2)["hgb_v2"], E.component_deltas(ctx)["hgb_v2"]
    )


def test_ensemble_unavailable_before_its_fit_window_falls_back_to_hgb(svc):
    _trained(svc)
    _write_weights(svc, model_for([0.2] * 12, available_from="2026-05-01"))
    as_of = date(2026, 4, 30)
    assert not svc.model_ready("ensemble", as_of)
    # Before the weights' window the service runs the next honest model and says so.
    result = svc.forecast(ForecastRequest(account_id="A", as_of=as_of, model="ensemble"))
    assert result.model == "hgb"


def test_ensemble_without_weights_artifact_falls_back(svc):
    _trained(svc)
    as_of = date(2026, 4, 30)
    assert not svc.model_ready("ensemble", as_of)
    result = svc.forecast(ForecastRequest(account_id="A", as_of=as_of, model="ensemble"))
    assert result.model == "hgb"
    # A pinned model version is never swapped silently.
    with pytest.raises(ValueError, match="build-ensemble"):
        svc.forecast(
            ForecastRequest(account_id="A", as_of=as_of, model="ensemble", model_version="ens-x")
        )


def test_error_bank_dispatches_to_ensemble_file(tmp_path):
    as_of = date(2026, 4, 1)
    points = assemble(as_of, 100, np.zeros((30, 3)), [])
    rows = [
        {
            "account_id": f"C{i % 6}",
            "origin": "2026-01-01",
            "available_to": "2026-01-31",
            "recipe": "ensemble|ens-x",
            "normalized_balance_error": [-200.0] * 30,
        }
        for i in range(24)
    ]
    (tmp_path / "ensemble").mkdir()
    (tmp_path / "ensemble" / "error_bank.json").write_text(json.dumps(rows))
    for calendar in (True, False):
        risk = add_uncertainty(
            deterministic_risk(100, points), points, as_of, f"ensemble|ens-x|calendar={calendar}",
            1, tmp_path, False,
        )  # fmt: skip
        assert risk.probability_30d == 1.0 and risk.trajectory_count == 24
    other = add_uncertainty(
        deterministic_risk(100, points), points, as_of, "ensemble|ens-y|calendar=True", 1,
        tmp_path, False,
    )  # fmt: skip
    assert other.probability_30d is None


def test_fold_origins_end_before_training_cutoff():
    origins = E.fold_origins(date(2025, 6, 30), date(2025, 12, 31), 7)
    assert origins[0] == date(2025, 7, 1)
    assert max(origins) + timedelta(days=30) <= date(2025, 12, 31)
    assert all(b - a == timedelta(days=7) for a, b in zip(origins, origins[1:], strict=False))


def test_protocol_hgb_cal_is_the_product_default_path(svc):
    # The client sends model='hgb', calendar=true: X10 measures exactly this path as hgb_cal.
    data = _trained(svc)
    as_of = date(2026, 4, 30)
    result = svc.forecast(ForecastRequest(account_id="A", as_of=as_of, model="hgb", calendar=True))
    txs, daily = data["A"]
    past = p3.categories_at([t for t in txs if t["operation_date"] <= str(as_of)], as_of)
    visible = [d for d in daily if d["date"] <= str(as_of)]
    ctx = p3.Context("A", as_of, result.opening_balance_minor, svc.registry, {}).bind(past, visible)
    forecast = p3.METHODS["hgb_cal"](past, visible, as_of, ctx)
    assert forecast.balances_minor == [p.closing_balance_minor for p in result.points]
