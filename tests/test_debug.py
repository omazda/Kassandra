"""Debug datasets: a hackathon-format upload in its own namespace, its report, check and removal."""

import io
import zipfile
from datetime import date

import pytest
from conftest import OWNER, csv_bytes, transaction

from cashflow_services.debug import MAX_DEBUG_DATASETS, dataset_files

DEMO = {"id": "demo", "accounts": []}


def bad_rows():
    return [
        transaction(account="B", operation="b1", day="2026-13-01"),
        transaction(account="B", operation="b2", amount="-5.00"),
        transaction(account="B", operation="b3", inn="12345"),
    ]


def archive(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


COMPANIES = "﻿company_id;company_name\nA;ООО «Ромашка»\n".encode()


def test_upload_keeps_good_rows_and_lists_bad_ones(svc, rows):
    report = svc.debug_upload("ops.csv", csv_bytes(rows + bad_rows()))
    assert report["id"] == "dbg1"
    assert report["rows"] == len(rows) + 3 and report["accepted"] == len(rows)
    assert report["rejected"] == 3
    assert [e["line"] for e in report["errors"]] == [len(rows) + 2, len(rows) + 3, len(rows) + 4]
    assert report["errors"][0]["reason"] == "Такой даты нет в календаре"
    # The upload never touches the existing account A: it lives under dbg1-.
    assert [a["account_id"] for a in report["accounts"]] == ["dbg1-A"]
    assert svc.store.quality("A")["operations"] == len(rows)
    assert report["companies"] == 1 and report["first_date"] == "2025-10-01"
    assert report["categories"]["top"][0]["operations"] > 0
    # Debug accounts open in the demo session, so the page can link to their forecast.
    assert "dbg1-A" in {a["id"] for a in svc.accounts(DEMO)}


def test_zip_reads_the_top_copy_once_with_company_names(svc, rows):
    payload = archive(
        {
            "dataset/all_operations.csv": csv_bytes(rows),
            "dataset/dataset/all_operations.csv": csv_bytes(rows),
            "dataset/companies.csv": COMPANIES,
        }
    )
    report = svc.debug_upload("dataset.zip", payload)
    assert report["accepted"] == len(rows) and report["duplicates"] == 0
    assert report["source"] == "dataset/all_operations.csv из архива"
    assert report["accounts"][0]["name"] == "ООО «Ромашка»"


def test_zip_of_statements_is_joined_under_one_header(rows):
    first, second = rows[:10], rows[10:20]
    for row in second:
        row["company_id"] = "B"
    payload = archive({"statements/A.csv": csv_bytes(first), "statements/B.csv": csv_bytes(second)})
    operations, names, source = dataset_files(payload)
    assert source == "2 выписок statements/*.csv из архива" and names == {}
    assert operations.decode().count("company_id") == 1
    assert len(operations.decode().strip().splitlines()) == 21


def test_check_compares_a_past_forecast_with_the_fact(svc, rows):
    report = svc.debug_upload("ops.csv", csv_bytes(rows))
    check = svc.debug_check("dbg1-A", model="weekday")
    assert check["as_of"] == date(2026, 5, 28)
    test = check["backtest"]
    assert test["as_of"] == date(2026, 4, 28) and test["days"] == 30
    assert test["mae_minor"] >= 0 and test["mae_constant_minor"] > 0
    assert test["skill"] == round(1 - test["mae_minor"] / test["mae_constant_minor"], 3)
    assert (
        report["accounts"][0]["last_balance_minor"]
        == svc.store.daily("dbg1-A")[-1]["balance_minor"]
    )


def test_check_says_why_there_is_no_backtest(svc, rows):
    svc.debug_upload("ops.csv", csv_bytes(rows[:100]))
    check = svc.debug_check("dbg1-A", model="weekday")
    assert check["backtest"] is None and "90 дней" in check["backtest_note"]


def test_check_is_only_for_debug_accounts(svc):
    with pytest.raises(PermissionError):
        svc.debug_check("A", model="weekday")


def test_delete_removes_the_set_and_the_limit_counts_sets(svc, rows):
    for _ in range(MAX_DEBUG_DATASETS):
        svc.debug_upload("ops.csv", csv_bytes(rows[:50]))
    with pytest.raises(PermissionError):
        svc.debug_upload("ops.csv", csv_bytes(rows[:50]))
    svc.debug_check("dbg2-A", model="weekday")
    assert svc.debug_delete("dbg2") == {"id": "dbg2", "removed_accounts": 1}
    with svc.store.connect() as db:
        left = db.execute("SELECT COUNT(*) FROM transactions WHERE account_id='dbg2-A'").fetchone()[
            0
        ]
        runs = db.execute("SELECT COUNT(*) FROM forecasts WHERE account_id='dbg2-A'").fetchone()[0]
    assert left == 0 and runs == 0
    assert [d["id"] for d in svc.debug_datasets()] == ["dbg1", "dbg3"]
    assert svc.debug_upload("ops.csv", csv_bytes(rows[:50]))["id"] == "dbg4"
    assert svc.store.quality("A")["operations"] == len(rows)


def test_api_is_off_by_default_and_validates_responses(svc, rows, monkeypatch):
    from fastapi.testclient import TestClient

    from apps.api.main import create_app

    def client():
        return TestClient(create_app(svc, users={"t": OWNER}, auth_mode="demo"))

    assert client().get("/api/v1/debug/datasets").status_code == 404
    monkeypatch.setenv("CASHFLOW_DEBUG_DATASETS", "true")
    api = client()
    uploaded = api.post(
        "/api/v1/debug/datasets",
        files={"file": ("ops.csv", csv_bytes(rows + bad_rows()), "text/csv")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["rejected"] == 3
    listed = api.get("/api/v1/debug/datasets").json()
    assert [d["id"] for d in listed] == ["dbg1"] and "accounts" not in listed[0]
    assert api.get("/api/v1/debug/datasets/dbg1").json()["accounts"][0]["account_id"] == "dbg1-A"
    assert api.delete("/api/v1/debug/datasets/dbg1").json()["removed_accounts"] == 1


def test_check_falls_back_when_the_main_model_would_see_the_future(svc, rows):
    # The test service has no trained hgb at all: the check must still answer, honestly labelled.
    svc.debug_upload("ops.csv", csv_bytes(rows))
    check = svc.debug_check("dbg1-A")
    assert check["model"] == "weekday" and check["backtest"]["days"] == 30
