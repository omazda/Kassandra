"""Checkpoint path: a control file in a near-hackathon format, history merge, the whole-dataset
check with its step log and metrics, the forecast trace and the X11 alarm calibration."""

import json
import time
from datetime import date

import pytest
from conftest import OWNER, csv_bytes

from cashflow_domain.schemas import ForecastRequest
from cashflow_ml.risk import calibrate_alarm
from cashflow_services.debug import normalize_operations
from cashflow_services.evaluation_job import compute_metrics

DEMO = {"id": "demo", "accounts": []}


def wait(svc, dataset_id):
    for _ in range(600):
        state = svc.evaluation(dataset_id)
        if state["status"] != "running":
            return state
        time.sleep(0.05)
    raise AssertionError("расчёт не завершился")


def test_normalize_reads_a_bank_export_with_russian_columns():
    text = (
        "Дата;Сумма;Назначение платежа;Остаток на конец дня\n"
        "01.03.2026;1 500,50;Оплата по счёту 7;10 000,00\n"
        "02.03.2026;-200,00;Аренда;9 800,00\n"
    )
    data, notes = normalize_operations(text.encode("cp1251"), "C900")
    lines = data.decode().strip().splitlines()
    assert lines[0].split(";")[:5] == [
        "company_id",
        "operation_id",
        "operation_date",
        "amount",
        "direction",
    ]
    first, second = (line.split(";") for line in lines[1:])
    assert first[:5] == ["C900", "C900-000001", "2026-03-01", "1500.50", "Поступление"]
    assert second[3:5] == ["200.00", "Списание"] and second[-1] == "9800.00"
    assert "кодировка Windows-1251" in notes and "направление по знаку суммы" in notes


def test_normalize_keeps_a_canonical_file_as_is(rows):
    payload = csv_bytes(rows[:5])
    assert normalize_operations(payload, "X") == (payload, [])


def test_normalize_names_missing_columns():
    with pytest.raises(ValueError, match="balance_eod"):
        normalize_operations(b"date,amount\n2026-03-01,5\n", "X")


def test_control_tail_gets_history_from_the_main_dataset(svc, rows):
    tail = [r for r in rows if r["operation_date"] >= "2026-04-01"]
    report = svc.debug_upload("control.csv", csv_bytes(tail))
    account = report["accounts"][0]
    assert report["accepted"] == len(tail) and report["history_companies"] == 1
    assert account["data_from"] == "2026-04-01" and account["history_copied"] > 0
    assert report["first_date"] == "2026-04-01"
    # The model sees the copied past; the main account A is not touched.
    assert svc.store.quality("dbg1-A")["first_date"] == "2025-10-01"
    assert svc.store.quality("A")["operations"] == len(rows)
    bare = svc.debug_upload("control.csv", csv_bytes(tail), history=False)
    assert bare["history_companies"] == 0
    assert svc.store.quality(f"{bare['id']}-A")["first_date"] == "2026-04-01"


def window(start, forecast, actual, *, gap=None, alarm_day=None, line=None, p=None):
    return {
        "account_id": "A",
        "start_balance": start,
        "forecast": forecast,
        "actual": actual,
        "actual_gap_day": gap,
        "predicted_gap_day": alarm_day,
        "trajectory_gap_day": line,
        "probability": p,
        "alarm": None,
    }


def test_metrics_count_errors_confusion_and_probability():
    windows = [
        # Hit: gap on day 2, alarm on day 2.
        window(100, [50, -10], [40, -20], gap=2, alarm_day=2, line=2, p=0.9),
        # False alarm: the forecast dips, the fact does not.
        window(100, [90, -5], [100, 100], alarm_day=2, line=2, p=0.6),
        # Miss: the gap came without an alarm.
        window(100, [100, 100], [10, -1], gap=2, p=0.2),
        # Quiet window, a masked day is skipped in the errors.
        window(100, [100, 110], [100, None], p=0.1),
        # Started in a deficit: out of the gap metrics, still in the balance error.
        window(-5, [-5, -5], [-5, -5], gap=1, alarm_day=1, line=1, p=1.0),
    ]
    m = compute_metrics(windows)
    b = m["balance"]
    assert b["points"] == 9 and b["windows"] == 5
    errors = [10, 10, 10, 105, 90, 101, 0, 0, 0]
    assert b["mae_minor"] == round(sum(errors) / 9)
    assert b["mse"] == pytest.approx(sum(e * e for e in errors) / 9)
    g = m["gaps"]
    assert g["confusion"] == {"tp": 1, "fp": 1, "fn": 1, "tn": 1}
    assert g["skipped_negative_start"] == 1
    assert (g["precision"], g["recall"], g["f1"], g["accuracy"]) == (0.5, 0.5, 0.5, 0.5)
    assert g["date_within_3_days"] == 1.0 and g["lead_days_median"] == 2
    p = m["probability"]
    assert p["windows"] == 4 and p["base_rate"] == 0.5
    assert p["brier"] == pytest.approx(((0.1) ** 2 + 0.6**2 + 0.8**2 + 0.1**2) / 4, abs=1e-4)
    # The positives 0.9 and 0.2 against the negatives 0.6 and 0.1: 3 of 4 pairs ranked right.
    assert p["roc_auc"] == 0.75


def test_evaluation_logs_steps_and_stores_companies(svc, rows):
    report = svc.debug_upload("ops.csv", csv_bytes(rows))
    assert svc.evaluation(report["id"])["status"] == "none"
    started = svc.start_evaluation(report["id"], DEMO)
    assert started["status"] in ("running", "done")
    state = wait(svc, report["id"])
    assert state["status"] == "done", state["error"]
    steps = [line for line in state["log"] if "Шаг" in line]
    assert len(steps) == 8 and "Шаг 8. Разрывы" in steps[-1]
    result = state["result"]
    assert result["current"]["companies"] == 1
    assert result["metrics"]["balance"]["windows"] == 4
    assert set(result["metrics"]) == {"balance", "gaps", "gaps_trajectory", "probability"}
    company = svc.evaluation_company(report["id"], "dbg1-A")
    assert len(company["forecast"]) == 30 and len(company["windows"]) == 4
    first = company["windows"][0]
    assert first["origin"] == "2026-04-28" and len(first["actual"]) == 30
    assert [s["n"] for s in company["trace"]] == list(range(1, 9))
    text = svc.evaluation_report(report["id"])
    assert "| Модель предсказала разрыв | TP =" in text and "## Журнал расчёта" in text
    # The state survives a restart of the service: it lives in SQLite.
    assert json.loads(json.dumps(svc.evaluation(report["id"])))["result"]["computed_at"]


def test_evaluation_api_and_trace(svc, rows, monkeypatch):
    from fastapi.testclient import TestClient

    from apps.api.main import create_app

    monkeypatch.setenv("CASHFLOW_DEBUG_DATASETS", "true")
    api = TestClient(create_app(svc, users={"t": OWNER}, auth_mode="demo"))
    up = api.post(
        "/api/v1/debug/datasets?history=false",
        files={"file": ("ops.csv", csv_bytes(rows), "text/csv")},
    )
    assert up.status_code == 200, up.text
    dataset = up.json()["id"]
    assert api.post(f"/api/v1/debug/datasets/{dataset}/evaluate").status_code == 200
    wait(svc, dataset)
    state = api.get(f"/api/v1/debug/datasets/{dataset}/evaluation")
    assert state.status_code == 200 and state.json()["status"] == "done"
    company = api.get(f"/api/v1/debug/datasets/{dataset}/evaluation/companies/{dataset}-A")
    assert company.status_code == 200, company.text
    run = company.json()["current"]["run_id"]
    trace = api.get(f"/api/v1/forecasts/{run}/trace")
    assert trace.status_code == 200 and trace.json()[0]["title"] == "История операций"
    md = api.get(f"/api/v1/debug/datasets/{dataset}/evaluation/report.md")
    assert md.status_code == 200 and "attachment" in md.headers["content-disposition"]


def test_alarm_calibration_respects_the_fit_period(svc, tmp_path):
    forecast = svc.forecast(ForecastRequest(account_id="A", as_of=date(2026, 5, 1)))
    artifact = {
        "available_to": "2026-06-23",
        "threshold": 0.26,
        "windows": 780,
        "iso_x": [0.0, 0.5, 1.0],
        "iso_y": [0.0, 0.2, 0.6],
    }
    (tmp_path / "gap_alarm.json").write_text(json.dumps(artifact))
    risk = forecast.risk.model_copy(update={"probability_30d": 0.75})
    early = calibrate_alarm(risk.model_copy(), date(2026, 6, 23), tmp_path)
    assert early.probability_30d == 0.75 and early.alarm is None
    late = calibrate_alarm(risk.model_copy(), date(2026, 7, 1), tmp_path)
    assert late.probability_30d == pytest.approx(0.4) and late.alarm is True
    assert late.calibration_status == "calibrated_on_selection"
    quiet = calibrate_alarm(
        risk.model_copy(update={"probability_30d": 0.4}), date(2026, 7, 1), tmp_path
    )
    assert quiet.probability_30d == pytest.approx(0.16) and quiet.alarm is False


def test_closed_account_is_not_a_gap_ahead(svc, rows):
    closed = [dict(r, company_id="B") for r in rows if r["operation_date"] <= "2026-03-01"]
    last_day = closed[-1]["operation_date"]
    for r in closed:
        if r["operation_date"] == last_day:
            r["balance_eod"] = "0.00"
    report = svc.debug_upload("two.csv", csv_bytes(rows + closed), history=False)
    svc.start_evaluation(report["id"], DEMO)
    result = wait(svc, report["id"])["result"]
    flags = {c["account_id"]: c["closed"] for c in result["companies"]}
    assert flags == {"dbg1-A": False, "dbg1-B": True}
    assert result["current"]["closed"] == 1


def test_history_is_not_glued_to_another_company_with_the_same_id(svc, rows):
    tail = [r for r in rows if r["operation_date"] >= "2026-04-01"]
    # Same id «A», but neither the balance nor the counterparties continue it: another firm.
    other = [
        dict(r, balance_eod=str(float(r["balance_eod"]) + 5000), counterparty_inn="0099999999")
        for r in tail
    ]
    report = svc.debug_upload("control.csv", csv_bytes(other))
    assert report["history_companies"] == 0
    assert any("история не добавлена" in note and "A" in note for note in report["notes"])
    assert svc.store.quality("dbg1-A")["first_date"] == "2026-04-01"


def test_history_follows_the_counterparties_across_a_skipped_day(svc, rows):
    # The same firm, but the statement skips days: the balance jumps, the counterparties match.
    tail = [
        dict(r, balance_eod=str(float(r["balance_eod"]) + 5000))
        for r in rows
        if r["operation_date"] >= "2026-04-01"
    ]
    report = svc.debug_upload("control.csv", csv_bytes(tail))
    assert report["history_companies"] == 1 and not report["notes"]


def test_packaged_alarm_calibrator_is_used_without_an_artifact(svc, tmp_path):
    forecast = svc.forecast(ForecastRequest(account_id="A", as_of=date(2026, 5, 1)))
    risk = forecast.risk.model_copy(update={"probability_30d": 0.95})
    calibrated = calibrate_alarm(risk, date(2026, 7, 1), tmp_path)
    assert calibrated.calibration_status == "calibrated_on_selection"
    assert calibrated.alarm is True and calibrated.probability_30d < 0.95
