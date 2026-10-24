import json
from datetime import date

import pytest

from cashflow_domain.engine import assemble, deterministic_risk
from cashflow_ml.risk import add_uncertainty


def test_whole_paths_are_resampled_and_future_errors_ineligible(tmp_path):
    points = assemble(date(2026, 4, 1), 1000, [[0, 0, 0]] * 30, [])
    risk = deterministic_risk(1000, points)
    rows = [
        {
            "recipe": "weekday",
            "account_id": str(i % 5),
            "available_to": "2026-03-01",
            "normalized_balance_error": [-2000] * 30,
        }
        for i in range(20)
    ]
    (tmp_path / "error_bank.json").write_text(json.dumps(rows))
    risk = add_uncertainty(risk, points, date(2026, 4, 1), "weekday", 1, tmp_path, False)
    assert risk.probability_30d == 1 and risk.calibration_status == "experimental_uncalibrated"
    assert risk.simulations == 1000 and risk.trajectory_count == 20
    assert all(p.p10_minor == -1000 and p.p90_minor == -1000 for p in points)
    risk = add_uncertainty(
        deterministic_risk(1000, points), points, date(2026, 2, 1), "weekday", 1, tmp_path, False
    )
    assert risk.probability_30d is None
    # With registered events the probability stays, and the assumption is stated explicitly.
    risk = add_uncertainty(
        deterministic_risk(1000, points), points, date(2026, 4, 1), "weekday", 1, tmp_path, True
    )
    assert risk.probability_30d == 1 and risk.calibration_status == "experimental_uncalibrated"
    assert risk.limitation.startswith("События реестра считаются надёжными")


# ---------------------------------------------------------------- X6: калибровка и симуляция

import numpy as np  # noqa: E402

from cashflow_ml import calibration as C  # noqa: E402


class _Ev:
    def __init__(self, day, amount, direction="out", source="regular", as_of=date(2026, 4, 1)):
        self.expected_date = as_of.replace(day=1) + __import__("datetime").timedelta(days=day)
        self.known_at, self.status, self.source = as_of, "assumption", source
        self.remaining_minor, self.direction = amount, direction


def test_conformal_bands_cover_exchangeable_errors():
    rng = np.random.default_rng(1)
    errors = np.cumsum(rng.normal(0, 0.05, size=(600, 30)), axis=1)
    bands = C.conformal_bands(errors[:300])
    inside = [
        np.mean((e >= np.asarray(bands["lo"])) & (e <= np.asarray(bands["hi"])))
        for e in errors[300:]
    ]
    assert 0.74 <= np.mean(inside) <= 0.86
    lo, hi = C.apply_bands(np.full(30, 1000.0), 100.0, bands)
    assert np.all(lo <= 1000) and np.all(hi >= 1000)
    with pytest.raises(ValueError):
        C.conformal_bands(errors[:5])


def test_simulation_whole_paths_and_event_jitter():
    central = np.full(30, 500.0)
    bank = np.zeros((10, 30))
    sim = C.simulate(central, 1.0, bank, [], 0, jitter=True)
    assert sim["p"] == 0 and sim["offset"] is None
    # ошибка целой траектории -600 пересекает порог со дня 1
    sim = C.simulate(central, 1.0, np.full((10, 30), -600.0 / 1.0), [], 0)
    assert sim["p"] == 1 and sim["offset"] == 1 and sim["deficit_minor"] == 100 * 10 - 900
    # платёж 700 в день 10 с разбросом даты; без разброса баланс падает ровно со дня 10
    central = np.where(np.arange(30) >= 9, 500.0 - 700, 500.0)
    fixed = C.simulate(central, 1.0, bank, [(10, -700, 0, 0)], 0)
    assert fixed["p"] == 1 and fixed["offset"] == 10
    moved = C.simulate(central, 1.0, bank, [(10, -700, 3, 0)], 0)
    assert moved["p"] == 1 and 7 <= moved["offset"] <= 13
    # событие внутри окна, сдвинутое на горизонт, не создаёт пересечения раньше срока
    assert C.simulate(central, 1.0, bank, [(10, -700, 3, 0)], 0, jitter=False)["offset"] == 10


def test_event_specs_skip_unknown_and_use_source_params():
    as_of = date(2026, 4, 1)
    ev = [_Ev(5, 100), _Ev(6, 50, "in", "documents"), _Ev(40, 10)]
    ev[0].known_at = date(2026, 4, 2)  # ещё не известно на as_of
    specs = C.event_specs(ev, as_of)
    assert len(specs) == 1
    assert specs[0][1] == 50 and specs[0][2] == C.DEFAULT_SOURCE_PARAMS["documents"]["half_days"]


def test_calibrators_monotone_and_oof_by_company():
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, 400)
    y = (rng.uniform(0, 1, 400) < p**2).astype(float)  # переоценённая вероятность
    groups = [f"A{i % 20}" for i in range(400)]
    for kind in ("isotonic", "platt"):
        cal = C.fit_calibrator(p, y, kind)
        grid = cal(np.linspace(0, 1, 50))
        assert np.all(np.diff(grid) >= -1e-9)
        assert C.Calibrator.from_dict(cal.to_dict())(0.5) == pytest.approx(float(cal(0.5)))
        oof = C.cross_validated(p, y, groups, kind)
        assert C.brier(oof, y) < C.brier(p, y)


def test_alarm_threshold_respects_budget():
    p = np.array([0.9, 0.8, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1] * 10)
    y = np.array([1, 1, 0, 1, 0, 0, 0, 0] * 10)
    open_mask = np.ones(80, bool)
    step = C.MONTH_DAYS  # одно окно = один счёт-месяц
    t = C.pick_alarm_threshold(p, y, open_mask, step, budget=1.0)
    false = np.sum((p >= t) & (y == 0))
    assert false / 80 <= 1.0 and t <= 0.1
    strict = C.pick_alarm_threshold(p, y, open_mask, step, budget=0.1)
    assert np.sum((p >= strict) & (y == 0)) / 80 <= 0.1 and strict > t


def test_reliability_status_rule():
    bins = [
        {"n": 100, "mean_p": 0.05, "observed": 0.10},
        {"n": 60, "mean_p": 0.5, "observed": 0.55},
        {"n": 5, "mean_p": 0.9, "observed": 0.2},  # мало окон, не проверяется
    ]
    assert C.reliability_status(bins)["ok"]
    bins[1]["observed"] = 0.7
    assert not C.reliability_status(bins)["ok"]
    assert not C.reliability_status(bins[:1])["ok"]


def _artifact(tmp_path, ok=True, available_to="2026-02-28"):
    bank = [[-0.001 * h for h in range(30)]] * 25
    bands = C.conformal_bands(np.asarray(bank) + np.random.default_rng(0).normal(0, 0.01, (25, 30)))
    variant = {
        "bank": bank,
        "bank_size": 25,
        "conformal": bands,
        "jitter": True,
        "calibrator": {"kind": "isotonic", "x": [0.0, 1.0], "y": [0.0, 0.5]},
        "status": {"ok": ok},
    }
    data = {
        "available_to": available_to,
        "simulations": 200,
        "source_params": C.DEFAULT_SOURCE_PARAMS,
        "variants": {"docs": variant, "nodocs": variant},
    }
    path = tmp_path / C.FILE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data))


def test_product_probability_status_and_dates(tmp_path):
    as_of = date(2026, 4, 1)
    points = assemble(as_of, 1000, [[0, 0, 100]] * 30, [])  # остаток падает ниже нуля
    _artifact(tmp_path, ok=True)
    risk = C.add_calibrated_uncertainty(
        deterministic_risk(1000, points), points, [], as_of, 1000.0, tmp_path, True
    )
    assert risk.probability_30d == pytest.approx(0.5)  # изотония: 1 -> 0.5
    assert risk.calibration_status == "calibrated_on_jan_feb"
    assert all(p.p10_minor <= p.closing_balance_minor <= p.p90_minor for p in points)
    _artifact_fail = tmp_path / "b"
    _artifact(_artifact_fail, ok=False)
    risk = C.add_calibrated_uncertainty(
        deterministic_risk(1000, points), points, [], as_of, 1000.0, _artifact_fail, False
    )
    assert risk.calibration_status == "experimental_uncalibrated"
    scen = C.add_calibrated_uncertainty(
        deterministic_risk(1000, points), points, [], as_of, 1000.0, tmp_path, True, scenario=True
    )
    assert "Сценар" in scen.probability_source and "сценар" in scen.limitation
    # до окончания целей калибровки (as_of <= available_to) вероятность недоступна
    early = C.add_calibrated_uncertainty(
        deterministic_risk(1000, points), points, [], date(2026, 2, 28), 1000.0, tmp_path, True
    )
    assert early.probability_30d is None and early.calibration_status == "unavailable"
    # нет артефакта
    assert (
        C.add_calibrated_uncertainty(
            deterministic_risk(1000, points), points, [], as_of, 1000.0, tmp_path / "none", True
        ).probability_30d
        is None
    )


def test_add_uncertainty_dispatches_structural(tmp_path):
    as_of = date(2026, 4, 1)
    points = assemble(as_of, 1000, [[0, 0, 100]] * 30, [])
    _artifact(tmp_path)
    risk = add_uncertainty(
        deterministic_risk(1000, points),
        points,
        as_of,
        "structural|v|calendar=False",
        1000.0,
        tmp_path,
        False,
        events=[],
        docs=True,
    )
    assert risk.probability_30d is not None
    risk = add_uncertainty(
        deterministic_risk(1000, points),
        points,
        as_of,
        "structural|v|calendar=False",
        1000.0,
        tmp_path,
        True,
        events=[],
        docs=True,
    )
    assert risk.probability_30d is None
