import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import date, timedelta

from cashflow_domain.money import to_minor
from cashflow_services.categories import categorize

REQUIRED = {
    "company_id",
    "operation_id",
    "operation_date",
    "amount",
    "direction",
    "counterparty_inn",
    "counterparty_name",
    "payment_purpose",
    "balance_eod",
}
DIRECTIONS = {"Поступление": "in", "Списание": "out", "in": "in", "out": "out"}


def _reader(payload: bytes) -> csv.DictReader:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Ожидается CSV UTF-8 с разделителем ;") from exc
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    if not REQUIRED.issubset(reader.fieldnames or []):
        raise ValueError(
            "CSV: отсутствуют обязательные столбцы: "
            + ", ".join(sorted(REQUIRED - set(reader.fieldnames or [])))
        )
    return reader


def parse_row(row: dict) -> dict:
    """One CSV row as a transaction; ValueError says what is wrong without echoing the data."""
    account, op_id = row["company_id"].strip(), row["operation_id"].strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", account) or not op_id or len(op_id) > 160:
        raise ValueError("Некорректный ID")
    day_text = row["operation_date"]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_text):
        raise ValueError("Дата должна быть YYYY-MM-DD")
    try:
        day = date.fromisoformat(day_text).isoformat()
    except ValueError as exc:
        raise ValueError("Такой даты нет в календаре") from exc
    amount = to_minor(row["amount"])
    balance = to_minor(row["balance_eod"])
    if amount <= 0:
        raise ValueError("Сумма должна быть положительной")
    direction = DIRECTIONS.get(row["direction"])
    if direction is None:
        raise ValueError("Неизвестное направление")
    inn = row["counterparty_inn"].strip()
    if inn and not re.fullmatch(r"\d{10}|\d{12}", inn):
        raise ValueError("ИНН должен быть строкой из 10 или 12 цифр")
    tx = dict(
        account_id=account,
        operation_id=op_id,
        operation_date=day,
        amount_minor=amount,
        direction=direction,
        counterparty_inn=inn,
        counterparty_name=row["counterparty_name"],
        payment_purpose=row["payment_purpose"],
        balance_eod_minor=balance,
    )
    tx["fingerprint"] = hashlib.sha256(
        json.dumps(tx, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    tx["category"] = categorize(tx["payment_purpose"], direction, tx["counterparty_name"])
    return tx


def parse_transactions(payload: bytes) -> list[dict]:
    reader = _reader(payload)
    result, seen = [], {}
    for number, row in enumerate(reader, 2):
        try:
            tx = parse_row(row)
            key = (tx["account_id"], tx["operation_id"])
            if key in seen and seen[key] != tx["fingerprint"]:
                raise ValueError("Одинаковый ID содержит разные операции")
            seen[key] = tx["fingerprint"]
            result.append(tx)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f"Ошибка CSV в строке {number}: {exc}") from exc
    if not result:
        raise ValueError("CSV не содержит операций")
    return result


def parse_transactions_lenient(payload: bytes, limit: int = 50) -> tuple[list[dict], dict]:
    """Debug import: keeps the valid rows and lists the bad ones instead of rejecting the file."""
    reader = _reader(payload)
    result, seen, errors, rejected, total = [], {}, [], 0, 0
    for number, row in enumerate(reader, 2):
        total += 1
        try:
            tx = parse_row(row)
            key = (tx["account_id"], tx["operation_id"])
            if key in seen and seen[key] != tx["fingerprint"]:
                raise ValueError("Одинаковый ID содержит разные операции")
            seen[key] = tx["fingerprint"]
            result.append(tx)
        except (ValueError, TypeError, AttributeError) as exc:
            rejected += 1
            if len(errors) < limit:
                errors.append({"line": number, "reason": str(exc)[:200]})
    return result, {"rows": total, "rejected": rejected, "errors": errors}


def daily_calendar(
    transactions: list[dict], missing: list[dict] | None = None
) -> tuple[list, dict]:
    if not transactions:
        return [], {}
    by_day = defaultdict(list)
    suspicious = defaultdict(list)
    for tx in transactions:
        by_day[tx["operation_date"]].append(tx)
        suspicious[
            (
                tx["operation_date"],
                tx["amount_minor"],
                tx["direction"],
                tx["counterparty_inn"],
                tx["payment_purpose"],
            )
        ].append(tx["operation_id"])
    dates = sorted(by_day)
    report = {
        "operations": len(transactions),
        "first_date": dates[0],
        "last_date": dates[-1],
        "inconsistent_transitions": [],
        "inconsistent_eod_days": [],
        "suspicious_duplicates": [v for v in suspicious.values() if len(v) > 1],
        "empty_purpose": sum(not t["payment_purpose"].strip() for t in transactions),
        "empty_inn": sum(not t["counterparty_inn"] for t in transactions),
    }
    daily, last_balance, prev_observed = [], None, None
    bad_ranges = []
    for day in dates:
        ops = by_day[day]
        net = sum(t["amount_minor"] * (1 if t["direction"] == "in" else -1) for t in ops)
        eods = {t["balance_eod_minor"] for t in ops}
        eod = ops[-1]["balance_eod_minor"]
        if len(eods) != 1:
            report["inconsistent_eod_days"].append(day)
            bad_ranges.append((day, day))
        if last_balance is not None and eod != last_balance + net:
            start = (date.fromisoformat(prev_observed) + timedelta(days=1)).isoformat()
            bad_ranges.append((start, day))
            report["inconsistent_transitions"].append(
                {
                    "previous_date": prev_observed,
                    "date": day,
                    "difference_minor": eod - last_balance - net,
                }
            )
        last_balance, prev_observed = eod, day
    start, end = (date.fromisoformat(dates[i]) for i in (0, -1))
    initial_ops = by_day[dates[0]]
    balance = initial_ops[-1]["balance_eod_minor"] - sum(
        t["amount_minor"] * (1 if t["direction"] == "in" else -1) for t in initial_ops
    )
    for offset in range((end - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        ops = by_day.get(day, [])
        inflow = sum(t["amount_minor"] for t in ops if t["direction"] == "in")
        outflow = sum(t["amount_minor"] for t in ops if t["direction"] == "out")
        if ops:
            balance = ops[-1]["balance_eod_minor"]
        # Carry within the observed range is an assumption; never create targets after the end.
        suspect = any(a <= day <= b for a, b in bad_ranges)
        confirmed = any(m["start_date"] <= day <= m["end_date"] for m in (missing or []))
        coverage = (
            "confirmed_missing"
            if confirmed
            else "suspected_missing"
            if suspect
            else "observed"
            if ops
            else "no_operations_unconfirmed"
        )
        daily.append(
            dict(
                date=day,
                inflow_minor=inflow,
                outflow_minor=outflow,
                balance_minor=balance,
                coverage=coverage,
                valid=not (suspect or confirmed),
                operation_count=len(ops),
            )
        )
    report["calendar_days"] = len(daily)
    report["unconfirmed_no_operation_days"] = sum(
        d["coverage"] == "no_operations_unconfirmed" for d in daily
    )
    report["invalid_days"] = sum(not d["valid"] for d in daily)
    return daily, report
