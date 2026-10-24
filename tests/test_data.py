import pytest
from conftest import OWNER, csv_bytes, transaction

from cashflow_domain.money import to_minor
from cashflow_services.data import categorize, daily_calendar, parse_transactions
from cashflow_services.service import ConflictError


def test_bom_decimal_inn_and_eod_not_summed():
    rows = [
        transaction(operation="a", amount="0.01", balance="1000.03"),
        transaction(operation="b", amount="0.02", balance="1000.03"),
    ]
    parsed = parse_transactions(csv_bytes(rows))
    assert parsed[0]["counterparty_inn"] == "0012345678"
    daily, report = daily_calendar(parsed)
    assert daily[0]["inflow_minor"] == 3
    assert daily[0]["balance_minor"] == 100003
    assert not report["inconsistent_transitions"]


@pytest.mark.parametrize("amount", ["0", "-1", "1.001", "NaN", "Infinity", "10000000000000000"])
def test_invalid_amount_rejected(amount):
    with pytest.raises(ValueError):
        parse_transactions(csv_bytes([transaction(amount=amount)]))


def test_bad_direction_and_format():
    with pytest.raises(ValueError):
        parse_transactions(csv_bytes([transaction(direction="неизвестно")]))
    with pytest.raises(ValueError):
        parse_transactions(b"company_id,amount\nA,100")
    with pytest.raises(ValueError):
        parse_transactions(csv_bytes([transaction(day="20260101")]))


def test_repeat_import_and_conflict_atomicity(svc, rows):
    before = svc.store.account("A")["data_version"]
    result = svc.import_transactions(csv_bytes(rows), OWNER)
    assert result["inserted"] == 0 and result["duplicates"] == len(rows)
    assert svc.store.account("A")["data_version"] == before
    new = transaction(operation="new", day="2026-05-28", balance="9999.00")
    changed = {**rows[0], "amount": "400.00"}
    with pytest.raises(ConflictError):
        svc.import_transactions(csv_bytes([new, changed]), OWNER)
    assert not any(t["operation_id"] == "new" for t in svc.store.transactions("A"))


def test_similar_different_ids_preserved():
    daily, report = daily_calendar(
        parse_transactions(csv_bytes([transaction(operation="a"), transaction(operation="b")]))
    )
    assert daily[0]["operation_count"] == 2
    assert report["suspicious_duplicates"] == [["a", "b"]]


def test_missing_is_not_synthetic_flow_and_flags_are_available_only_after_detection():
    first = transaction(day="2026-01-01", balance="1100.01")
    last = transaction(operation="later", day="2026-01-04", balance="5000.00")
    daily, report = daily_calendar(parse_transactions(csv_bytes([first, last])))
    assert report["inconsistent_transitions"][0]["difference_minor"] == 379998
    assert daily[1]["inflow_minor"] == 0
    assert daily[1]["coverage"] == "suspected_missing"
    past, past_report = daily_calendar(parse_transactions(csv_bytes([first])))
    assert past[0]["valid"] and not past_report["inconsistent_transitions"]
    confirmed, _ = daily_calendar(
        parse_transactions(csv_bytes([first, last])),
        [{"start_date": "2026-01-02", "end_date": "2026-01-03"}],
    )
    assert confirmed[1]["coverage"] == "confirmed_missing"


@pytest.mark.parametrize(
    ("purpose", "direction", "category"),
    [
        ("Перевод собственных средств", "in", "own_transfer"),
        ("Возврат оплаты", "in", "refund"),
        ("Взнос учредителя", "in", "owner_funding"),
        ("ЕНП", "out", "tax"),
        ("Единый налоговый платёж", "out", "tax"),
        ("Заработная плата", "out", "salary"),
        ("Аренда", "out", "rent"),
        ("Проценты по кредиту", "out", "debt_service"),
        ("", "in", "unknown"),
    ],
)
def test_category_priority(purpose, direction, category):
    assert categorize(purpose, direction) == category


def test_money_retains_cents():
    assert sum(to_minor("0.01") for _ in range(100)) == 100


def test_import_from_unpacked_folder(tmp_path, monkeypatch):
    from cashflow_services.cli import import_file
    from cashflow_services.service import Service

    folder = tmp_path / "dataset"
    (folder / "statements").mkdir(parents=True)
    (folder / "dataset").mkdir()
    rows = [transaction(account="Z1", operation="o1", balance="1100.01")]
    (folder / "all_operations.csv").write_bytes(csv_bytes(rows))
    (folder / "companies.csv").write_bytes(
        "company_id;company_name\nZ1;Тестовая компания\n".encode("utf-8-sig")
    )
    # Nested copies must never be imported: they carry a conflicting operation.
    bad = [transaction(account="Z2", operation="bad", balance="1.00")]
    (folder / "statements" / "Z2.csv").write_bytes(csv_bytes(bad))
    (folder / "dataset" / "all_operations.csv").write_bytes(csv_bytes(bad))
    monkeypatch.chdir(tmp_path)
    svc = Service(tmp_path / "db.sqlite", tmp_path / "artifacts")
    import_file(svc, folder, demo=True)
    with svc.store.connect() as db:
        ids = [r[0] for r in db.execute("SELECT id FROM accounts ORDER BY id")]
    assert ids == ["Z1"]
    assert (tmp_path / "experiments/results/dataset_manifest.json").exists()
