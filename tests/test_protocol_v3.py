import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from cashflow_domain.engine import assemble
from cashflow_ml import protocol_v3 as p3
from cashflow_ml.models import ModelRegistry, predict

START = date(2026, 1, 1)


def build(balances, financing=None, categories=None, valid=None, start=START):
    """Дневные строки и операции из ряда остатков; financing: {индекс дня: сумма}."""
    financing = financing or {}
    daily, txs = [], []
    for i, balance in enumerate(balances):
        day = start + timedelta(days=i)
        inflow = financing.get(i, 0)
        daily.append(
            {
                "date": str(day),
                "inflow_minor": inflow,
                "outflow_minor": 0,
                "balance_minor": balance,
                "valid": True if valid is None else valid[i],
            }
        )
        if inflow:
            txs.append(tx(day, inflow, "in", (categories or {}).get(i, "owner_funding")))
    return txs, daily


def tx(day, amount, direction, category, n=[0]):  # noqa: B006
    n[0] += 1
    return {
        "account_id": "A",
        "operation_id": f"op{n[0]}",
        "operation_date": str(day),
        "amount_minor": amount,
        "direction": direction,
        "category": category,
        "counterparty_inn": "1",
        "counterparty_name": "x",
        "payment_purpose": "",
    }


def test_negative_crossing_is_event_and_episode_merges():
    txs, daily = build([500, 100, -50, -80, -10, 40, 300, -20, 100])
    truth = p3.AccountTruth(txs, daily)
    starts = [(str(e["start"]), str(e["end"]), e["kind"]) for e in truth.events]
    assert starts == [
        ("2026-01-03", "2026-01-05", "negative_eod"),
        ("2026-01-08", "2026-01-08", "negative_eod"),
    ]
    assert truth.events[0]["deficit_minor"] == 80


def test_financing_rescue_is_event_and_not_double_counted():
    # День 3: остаток 700 после пополнения 800 -> без него -100 < 0: событие (б).
    txs, daily = build([300, 200, 700, 650, 600], financing={2: 800})
    truth = p3.AccountTruth(txs, daily)
    assert [(str(e["start"]), e["kind"], e["deficit_minor"]) for e in truth.events] == [
        ("2026-01-03", "financing", 100)
    ]
    # Пополнение, после которого и без него не отрицательно, события не даёт.
    txs, daily = build([300, 1200, 1150], financing={1: 1000})
    assert p3.AccountTruth(txs, daily).events == []


def test_operating_inflow_is_not_financing_and_rescue_extends_deficit_episode():
    txs, daily = build(
        [100, -200, 600, 590], financing={2: 900}, categories={2: "customer_payment"}
    )
    assert [(str(e["start"]), str(e["end"])) for e in p3.AccountTruth(txs, daily).events] == [
        ("2026-01-02", "2026-01-02")
    ]
    txs, daily = build([100, -200, 700, 690], financing={2: 900})
    events = p3.AccountTruth(txs, daily).events
    assert [(str(e["start"]), str(e["end"])) for e in events] == [("2026-01-02", "2026-01-03")]


def test_invalid_day_breaks_episode_and_unknown_predecessor_is_not_new():
    txs, daily = build([100, -20, -30, 10, -5], valid=[True, True, False, True, True])
    events = p3.AccountTruth(txs, daily).events
    assert [str(e["start"]) for e in events] == ["2026-01-02", "2026-01-05"]
    txs, daily = build([-20, -30, 50])
    assert p3.AccountTruth(txs, daily).events == []  # дефицит уже был на первом дне


def test_threshold_changes_events():
    txs, daily = build([500, 300, 200])
    assert p3.AccountTruth(txs, daily, 0).events == []
    assert [str(e["start"]) for e in p3.AccountTruth(txs, daily, 250).events] == ["2026-01-03"]


def test_d3_target_subtracts_future_financing_only():
    balances = [1000 + 10 * i for i in range(60)]
    balances[11:] = [b + 5000 for b in balances[11:]]
    txs, daily = build(balances, financing={10: 777, 11: 5000})
    truth = p3.AccountTruth(txs, daily)
    w = truth.window(START + timedelta(days=9))
    assert w["opening_minor"] == balances[9]
    # Пополнение 777 в день 10 (h=1) не входит в остаток 9 -> вычитается; 5000 тоже.
    assert w["target"][0] == balances[10] - 777
    assert w["target"][1] == balances[11] - 777 - 5000
    assert w["observed"][1] == balances[11]
    assert w["financing_total_minor"] == 5777
    # Финансирование до as_of уже в остатке и не вычитается.
    w2 = truth.window(START + timedelta(days=12))
    assert w2["target"][0] == balances[13]


def test_window_event_offsets_and_current_deficit():
    txs, daily = build([100] * 5 + [-10] * 3 + [100] * 40)
    truth = p3.AccountTruth(txs, daily)
    assert truth.window(START + timedelta(days=1))["event_offsets"] == [4]
    assert truth.window(START + timedelta(days=6))["current_deficit"] is True
    assert truth.window(START + timedelta(days=6))["event_offsets"] == []


def test_origins_from_p3_json_respect_calibration_boundary():
    config = json.loads(Path("experiments/p3.json").read_text())
    origins = p3.expand_origins(config)
    assert [str(d) for d in origins["calibration"]] == [
        "2026-01-01",
        "2026-01-08",
        "2026-01-15",
        "2026-01-22",
        "2026-01-29",
    ]
    assert str(origins["selection"][0]) == "2026-03-01" and str(origins["selection"][-1]) == (
        "2026-05-31"
    )
    assert len(origins["selection"]) == 14
    assert str(origins["retrospective"][0]) == "2026-07-01"
    assert str(origins["retrospective"][-1]) == "2026-08-26"
    assert max(origins["calibration"]) + timedelta(days=30) <= date(2026, 2, 28)
    config["splits"]["calibration"]["origins"].append("2026-02-20")
    with pytest.raises(ValueError):
        p3.expand_origins(config)


def row(account, origin, alarm, offsets, current=False, alarm_offset=None, **extra):
    return {
        "split": "selection",
        "account_id": account,
        "origin": origin,
        "method": "m",
        "current_deficit": current,
        "alarm": alarm,
        "alarm_offset": alarm_offset,
        "event_offsets": offsets,
        "first_event_offset": min(offsets) if offsets else None,
        "pred_deficit_minor": 1000,
        "actual_deficit_minor": 4000,
        "p_gap_30d": None,
        **extra,
    }


def test_recall_counts_event_once_and_requires_seven_days_warning():
    config = {}
    # Событие 2026-03-20 видно из трёх срезов: за 19, 12 и 5 дней. Тревоги на всех.
    rows = [
        row("A", "2026-03-01", True, [19], alarm_offset=18),
        row("A", "2026-03-08", True, [12], alarm_offset=12),
        row("A", "2026-03-15", True, [5], alarm_offset=5),
        # Событие, замеченное только за 3 дня, не засчитывается и не масштабируется.
        row("B", "2026-03-17", True, [3], alarm_offset=3),
        # Событие без тревоги.
        row("C", "2026-03-01", False, [10]),
    ]
    m = p3.risk_metrics(rows, config, 7)
    assert m["events_scorable"] == 2  # A@03-20 и C@03-11; B (3 дня) вне окна 7-30
    assert m["events_detected"] == 1 and m["recall_7d"] == 0.5
    assert m["false_alarm_windows"] == 0
    assert m["date_error_n"] == 4
    assert m["date_within_tolerance"] == 1.0  # |18-19|, |12-12|, |5-5|, |3-3| = 1,0,0,0 -> все <=3
    assert m["deficit_mae_rub"] == 30.0


def test_date_error_tolerance_and_false_alarms_and_current_deficit():
    rows = [
        row("A", "2026-03-01", True, [20], alarm_offset=10),  # ошибка 10 дней
        row("B", "2026-03-01", True, [], alarm_offset=4),  # ложная тревога
        row("B", "2026-03-08", True, [], current=True, alarm_offset=1),  # текущий дефицит
        row("C", "2026-03-01", False, []),
    ]
    m = p3.risk_metrics(rows, {}, 7)
    assert m["windows_at_risk"] == 3 and m["windows_current_deficit"] == 1
    assert m["false_alarm_windows"] == 2  # тревога на 10-й день при событии на 20-й тоже ложная
    assert m["date_within_tolerance"] == 0.0 and m["date_error_median_days"] == 10
    assert m["false_alarms_per_account_month"] == pytest.approx(2 / (3 * 7 / p3.MONTH_DAYS))
    assert m["recall_7d"] == 0.0  # дата тревоги далеко от даты события: не найдено


def test_constant_never_alarms_on_nonnegative_opening():
    window = {
        "opening_minor": 1000,
        "observed": np.full(30, 1000),
        "target": np.full(30, 1000),
        "event_offsets": [5],
        "current_deficit": False,
    }
    record = p3.window_record(
        "selection", "A", START, "constant", p3.Forecast([1000] * 30), window, 100.0, 0
    )
    assert record["alarm"] is False and record["mae_traj_minor"] == 0


def test_bootstrap_median_is_reproducible():
    values = list(np.linspace(-0.2, 0.4, 21))
    a, b = p3.bootstrap_median(values), p3.bootstrap_median(values)
    assert a == b and a["ci_low"] <= a["median"] <= a["ci_high"]
    assert a["median"] == pytest.approx(0.1) and a["n_companies"] == 21
    assert p3.bootstrap_median([])["median"] is None


def test_company_skill_uses_ratio_of_means():
    rows = [
        {"account_id": "A", "origin": "1", "e": 1.0},
        {"account_id": "A", "origin": "2", "e": 3.0},
        {"account_id": "B", "origin": "1", "e": 5.0},
    ]
    base = {
        ("A", "1"): {"e": 2.0},
        ("A", "2"): {"e": 2.0},
        ("B", "1"): {"e": 0.0},  # нулевая база: компания пропускается
    }
    assert p3.company_skills(rows, base, "e") == [0.0]


def test_probability_metrics_brier_and_reliability():
    rows = [
        row("A", "1", False, [3], p_gap_30d=0.9),
        row("A", "2", False, [], p_gap_30d=0.1),
        row("B", "1", False, [], p_gap_30d=0.1),
        row("B", "2", False, [], p_gap_30d=0.5, current=True),  # текущий дефицит не в счёт
    ]
    m = p3.probability_metrics(rows, 0.25)
    assert m["n"] == 3
    assert m["brier"] == pytest.approx((0.01 + 0.01 + 0.01) / 3)
    assert m["vs_calibration_rate"]["brier_base"] == pytest.approx(
        ((0.75**2) + 0.25**2 + 0.25**2) / 3
    )
    assert sum(b["n"] for b in m["reliability"]) == 3 and len(m["reliability"]) == 10
    assert p3.probability_metrics([row("A", "1", False, [])], 0.2) is None


def test_shape_profile_has_no_trend_and_ignores_future():
    as_of = date(2026, 4, 30)
    net, valid = {}, set()
    for i in range(200):
        d = date(2025, 10, 1) + timedelta(days=i)
        valid.add(str(d))
        net[str(d)] = 100 + (5000 if d.day == 15 else 0)
    profile, used = p3.shape_profile(net, valid, as_of)
    assert used == 3 and profile[-1] == pytest.approx(0, abs=1e-6)
    assert profile.argmax() in (13, 14, 15, 16)  # пик около 15-го числа
    for i in range(200):
        d = date(2025, 10, 1) + timedelta(days=i)
        if d > as_of:
            net[str(d)] = 10**9
    again, _ = p3.shape_profile(net, valid, as_of)
    assert np.allclose(profile, again)
    zero, used = p3.shape_profile(net, set(), as_of)
    assert used == 0 and not zero.any()


def synthetic_account(days=330):
    balance, txs, daily = 5_000_000, [], []
    rng = np.random.default_rng(1)
    for i in range(days):
        day = START - timedelta(days=150) + timedelta(days=i)
        inflow = int(rng.integers(0, 80_000)) * (1 if day.weekday() < 5 else 0)
        outflow = int(rng.integers(0, 90_000)) * (1 if day.weekday() < 5 else 0)
        if inflow:
            txs.append(tx(day, inflow, "in", "customer_payment"))
        if outflow:
            txs.append(tx(day, outflow, "out", "supplier"))
        balance += inflow - outflow
        daily.append(
            {
                "date": str(day),
                "inflow_minor": inflow,
                "outflow_minor": outflow,
                "balance_minor": balance,
                "valid": True,
            }
        )
    return txs, daily


def test_baselines_match_models_predict(tmp_path):
    txs, daily = synthetic_account()
    as_of = date(2026, 2, 1)
    past = [t for t in txs if t["operation_date"] <= str(as_of)]
    opening = next(d["balance_minor"] for d in daily if d["date"] == str(as_of))
    ctx = p3.Context("A", as_of, opening, ModelRegistry(tmp_path), {}).bind(past, daily)
    assert ctx.prepared(False) is ctx.prepared(False)  # кэш подготовки
    for name, model in [("constant", "constant"), ("mean60", "mean"), ("weekday", "weekday")]:
        values, comps, _, _, _ = predict(past, as_of, model, False, ModelRegistry(tmp_path))
        expected = [p.closing_balance_minor for p in assemble(as_of, opening, values, [])]
        assert p3.METHODS[name](past, daily, as_of, ctx).balances_minor == expected, name


def test_run_protocol_hides_future_masks_windows_and_isolates_failures(tmp_path):
    txs, daily = synthetic_account()
    data = {"A": (txs, daily), "B": synthetic_account()}
    seen = []

    def spy(transactions, visible, as_of, ctx):
        seen.append((max(t["operation_date"] for t in transactions), visible[-1]["date"], as_of))
        return p3.forecast_constant(transactions, visible, as_of, ctx)

    def fragile(transactions, visible, as_of, ctx):
        if as_of == date(2026, 2, 8):
            raise RuntimeError("boom")
        return p3.forecast_constant(transactions, visible, as_of, ctx)

    origins = {
        "calibration": [date(2026, 1, 1)],
        "selection": [date(2026, 2, 1), date(2026, 2, 8)],
    }
    config = {
        "splits": {
            "calibration": {"origins": ["2026-01-01"]},
            "selection": {"start": "2026-02-01", "end": "2026-02-08", "step_days": 7},
        }
    }
    result = p3.run_protocol(
        data,
        {"constant": p3.forecast_constant, "spy": spy, "fragile": fragile},
        origins,
        registry=ModelRegistry(tmp_path),
        config=config,
        out_dir=tmp_path / "out",
        n_jobs=1,
    )
    assert seen and all(t <= str(o) and v == str(o) for t, v, o in seen)
    # Окно 2026-02-08 выпало для всех методов, и число окон одинаково.
    counts = {m: sum(r["method"] == m for r in result["records"]) for m in ("constant", "spy")}
    assert len(set(counts.values())) == 1
    assert {d["origin"] for d in result["dropped_windows"]} == {"2026-02-08"}
    assert all(r["origin"] != "2026-02-08" for r in result["records"])
    assert (tmp_path / "out" / "baseline.md").exists() and (
        tmp_path / "out" / "windows.csv"
    ).exists()
    assert result["summary"]["selection"]["constant"]["risk"]["recall_7d"] in (None, 0.0)


def test_d1_decision_rule():
    def entry(skill_traj, skill_min, recall, fa):
        return {
            "skill_trajectory": {"median": skill_traj},
            "skill_minimum": {"median": skill_min},
            "risk": {"recall_7d": recall, "false_alarms_per_account_month": fa},
        }

    summary = {
        "weekday": entry(-0.05, 0.1, 0.5, 0.2),
        "good": entry(-0.01, 0.3, 0.6, 0.5),
        "noisy": entry(-0.01, 0.3, 0.9, 1.5),
        "constant": entry(0, 0, 0, 0),
    }
    assert p3.d1_decision(summary, "good", "weekday", {})["pass"] is True
    assert p3.d1_decision(summary, "noisy", "weekday", {})["checks"]["false_alarm_budget"] is False
    assert p3.d1_decision(summary, "constant", "weekday", {})["pass"] is False


def test_alarm_must_match_event_date_for_true_positive():
    rows = [
        row("A", "2026-03-01", True, [20], alarm_offset=19),  # в допуске
        row("B", "2026-03-01", True, [20], alarm_offset=5),  # далеко: не TP и ложная
    ]
    m = p3.risk_metrics(rows, {}, 7)
    assert m["events_detected"] == 1 and m["events_scorable"] == 2
    assert m["false_alarm_windows"] == 1
