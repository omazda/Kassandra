"""Debug datasets: a hackathon-format upload checked and imported into its own namespace.

Every upload gets an id (`dbg1`, `dbg2`, ...) that prefixes its account ids, so the jury's
C001...C150 never collide with the demo accounts and the whole set can be removed at once.
"""

import csv
import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import PurePosixPath

from cashflow_domain.schemas import CATEGORY_LABELS, ForecastRequest
from cashflow_services.data import parse_transactions_lenient

DEBUG_OWNER = "debug"
DEBUG_PRINCIPAL = {"id": DEBUG_OWNER, "accounts": ["*"]}
MAX_DEBUG_DATASETS = 3
MAX_UNPACKED_BYTES = 400 * 1024 * 1024
BACKTEST_DAYS = 30
# Share of the upload's counterparty INNs already seen in the main history of the same id.
SAME_COUNTERPARTIES = 0.3
HISTORY_FIELDS = (
    "operation_id",
    "operation_date",
    "amount_minor",
    "direction",
    "counterparty_inn",
    "counterparty_name",
    "payment_purpose",
    "balance_eod_minor",
    "fingerprint",
    "category",
)
MIN_HISTORY_DAYS = 90
# Tables keyed by account_id that a debug set may fill (forecasts and checks included).
ACCOUNT_TABLES = (
    "transactions",
    "category_revisions",
    "daily",
    "quality",
    "missing_coverage",
    "event_revisions",
    "payments",
    "forecasts",
    "scenarios",
    "integration_syncs",
    "ai_reviews",
)


def _members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = [m for m in archive.infolist() if not m.is_dir()]
    unpacked = sum(m.file_size for m in members if m.filename.lower().endswith(".csv"))
    if unpacked > MAX_UNPACKED_BYTES:
        raise ValueError("CSV в архиве больше 400 МБ в распакованном виде")
    return members


def _shallowest(members, predicate):
    # The hackathon archive repeats itself in dataset/dataset/: the top copy wins.
    found = [m for m in members if predicate(m.filename)]
    if not found:
        return []
    depth = min(m.filename.count("/") for m in found)
    return sorted((m for m in found if m.filename.count("/") == depth), key=lambda m: m.filename)


def _named(name):
    return lambda path: PurePosixPath(path).name.lower() == name


def _company_names(raw: bytes) -> dict:
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), delimiter=";")
    if not {"company_id", "company_name"} <= set(reader.fieldnames or []):
        return {}
    return {r["company_id"].strip(): r["company_name"].strip() for r in reader}


def _concat(parts: list[bytes]) -> bytes:
    texts = [p.decode("utf-8-sig").strip("\r\n") for p in parts]
    header = texts[0].splitlines()[0]
    body = [texts[0]]
    for text in texts[1:]:
        lines = text.splitlines()
        if lines and lines[0] != header:
            raise ValueError("Выписки в архиве с разными заголовками столбцов")
        body.append("\n".join(lines[1:]))
    return "\n".join(body).encode()


ALIASES = {
    "company_id": (
        "company_id",
        "company",
        "account",
        "account_id",
        "компания",
        "счет",
        "id компании",
    ),
    "operation_id": ("operation_id", "id", "operation", "id операции", "номер операции"),
    "operation_date": ("operation_date", "date", "дата", "дата операции"),
    "amount": ("amount", "sum", "сумма", "сумма операции"),
    "direction": ("direction", "type", "направление", "тип", "тип операции"),
    "counterparty_inn": ("counterparty_inn", "inn", "инн", "инн контрагента"),
    "counterparty_name": (
        "counterparty_name",
        "counterparty",
        "контрагент",
        "наименование контрагента",
    ),
    "payment_purpose": (
        "payment_purpose",
        "purpose",
        "description",
        "назначение",
        "назначение платежа",
    ),
    "balance_eod": (
        "balance_eod",
        "balance",
        "остаток",
        "остаток на конец дня",
        "остаток на счете",
    ),
}
CANONICAL = tuple(ALIASES)
INCOMING = {"поступление", "приход", "in", "credit", "кредит", "зачисление", "+"}
OUTGOING = {"списание", "расход", "out", "debit", "дебет", "-"}


def _key(name: str) -> str:
    return " ".join(name.strip().lower().replace("ё", "е").replace("_", " ").split())


def _number(text: str) -> str:
    text = (text or "").strip().replace("\u00a0", "").replace(" ", "")
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    return text.replace(",", "")


def _iso_date(text: str) -> str:
    text = (text or "").strip()[:10]
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text):
        d, m, y = text.split(".")
        return f"{y}-{m}-{d}"
    return text


def normalize_operations(raw: bytes, default_company: str) -> tuple[bytes, list[str]]:
    """Statement in the hackathon format, or close to it: another encoding or delimiter, column
    names in Russian, signed amounts, DD.MM.YYYY dates, no ids. Returns the canonical CSV."""
    notes = []
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1251")
        notes.append("кодировка Windows-1251")
    head = text.split("\n", 1)[0]
    delimiter = max((";", ",", "\t", "|"), key=head.count)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = next(reader, [])
    lookup = {_key(a): canon for canon, names in ALIASES.items() for a in names}
    columns = [lookup.get(_key(h)) for h in header]
    if delimiter == ";" and not notes and all(c in columns for c in CANONICAL):
        return raw, notes
    if delimiter != ";":
        notes.append(f"разделитель «{'tab' if delimiter == chr(9) else delimiter}»")
    renamed = [h for h, c in zip(header, columns, strict=True) if c and _key(h) != _key(c)]
    if renamed:
        notes.append("колонки распознаны по названию: " + ", ".join(renamed[:6]))
    missing = [c for c in ("operation_date", "amount", "balance_eod") if c not in columns]
    if missing:
        raise ValueError("Нет обязательных колонок: " + ", ".join(missing))
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(CANONICAL)
    signed = "direction" not in columns
    if signed:
        notes.append("направление по знаку суммы")
    if "company_id" not in columns:
        notes.append(f"одна компания: {default_company}")
    if "operation_id" not in columns:
        notes.append("номера операций присвоены по порядку")
    for number, record in enumerate(reader, 1):
        if not any(cell.strip() for cell in record):
            continue
        row = {c: v for c, v in zip(columns, record, strict=False) if c}
        amount = _number(row.get("amount", ""))
        direction = (row.get("direction") or "").strip().lower()
        if signed or not direction:
            direction = "Списание" if amount.startswith("-") else "Поступление"
        elif direction in INCOMING:
            direction = "Поступление"
        elif direction in OUTGOING:
            direction = "Списание"
        company = (row.get("company_id") or "").strip() or default_company
        writer.writerow(
            [
                company,
                (row.get("operation_id") or "").strip() or f"{company}-{number:06d}",
                _iso_date(row.get("operation_date", "")),
                amount.lstrip("-+"),
                direction,
                "".join(ch for ch in (row.get("counterparty_inn") or "") if ch.isdigit()),
                (row.get("counterparty_name") or "").strip(),
                (row.get("payment_purpose") or "").strip(),
                _number(row.get("balance_eod", "")),
            ]
        )
    return out.getvalue().encode("utf-8"), notes


def _continues(last_main: dict, uploaded: list[dict], tolerance_minor: int = 100) -> bool:
    """The uploaded statement picks up where the main one stopped: the balance before its first
    day equals the main balance at the end of the last earlier day (within a ruble)."""
    first_day = min(t["operation_date"] for t in uploaded)
    day = [t for t in uploaded if t["operation_date"] == first_day]
    net = sum(t["amount_minor"] if t["direction"] == "in" else -t["amount_minor"] for t in day)
    opening = day[0]["balance_eod_minor"] - net
    return abs(opening - last_main["balance_eod_minor"]) <= tolerance_minor


def dataset_files(payload: bytes) -> tuple[bytes, dict, str]:
    """Operations CSV, company names and a human description of what was read."""
    if not payload.startswith(b"PK\x03\x04"):
        return payload, {}, "CSV-файл"
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise ValueError("Архив повреждён") from exc
    with archive:
        members = _members(archive)
        companies = _shallowest(members, _named("companies.csv"))
        names = _company_names(archive.read(companies[0])) if companies else {}
        operations = _shallowest(members, _named("all_operations.csv"))
        if operations:
            return archive.read(operations[0]), names, f"{operations[0].filename} из архива"
        statements = _shallowest(
            members, lambda path: re.search(r"(^|/)statements/[^/]+\.csv$", path, re.I)
        )
        if not statements:
            raise ValueError("В архиве нет all_operations.csv и выписок statements/*.csv")
        parts = [archive.read(m) for m in statements]
        return _concat(parts), names, f"{len(parts)} выписок statements/*.csv из архива"


def _quality_totals(reports: list[dict]) -> dict:
    transitions = [t for r in reports for t in r.get("inconsistent_transitions", [])]
    return {
        "inconsistent_transitions": len(transitions),
        "material_transitions": sum(abs(t["difference_minor"]) > 1 for t in transitions),
        "inconsistent_eod_days": sum(len(r.get("inconsistent_eod_days", [])) for r in reports),
        "suspicious_duplicate_groups": sum(
            len(r.get("suspicious_duplicates", [])) for r in reports
        ),
        "empty_purpose": sum(r.get("empty_purpose", 0) for r in reports),
        "empty_inn": sum(r.get("empty_inn", 0) for r in reports),
        "invalid_days": sum(r.get("invalid_days", 0) for r in reports),
    }


def _categories(rows: list[dict]) -> dict:
    count, amount = Counter(), Counter()
    for tx in rows:
        count[tx["category"]] += 1
        amount[tx["category"]] += tx["amount_minor"]
    total = sum(amount.values()) or 1
    return {
        "unknown_operations": count["unknown"],
        "unknown_share": round(amount["unknown"] / total, 4),
        "top": [
            {
                "code": code,
                "label": CATEGORY_LABELS.get(code, code),
                "operations": count[code],
                "amount_minor": value,
            }
            for code, value in amount.most_common(8)
        ],
    }


class DebugMixin:
    def _debug_rows(self):
        with self.store.connect() as db:
            return [
                {"id": r["id"], **json.loads(r["report"])}
                for r in db.execute("SELECT id, report FROM debug_datasets ORDER BY created_at")
            ]

    def debug_datasets(self):
        return [{k: v for k, v in r.items() if k != "accounts"} for r in self._debug_rows()]

    def debug_dataset(self, dataset_id):
        for report in self._debug_rows():
            if report["id"] == dataset_id:
                return report
        raise ValueError("Набор не найден")

    def debug_upload(self, filename: str, payload: bytes, history: bool = True):
        existing = self._debug_rows()
        if len(existing) >= MAX_DEBUG_DATASETS:
            raise PermissionError(
                f"Загружено уже {MAX_DEBUG_DATASETS} набора: удалите один, чтобы загрузить новый"
            )
        operations, names, source = dataset_files(payload)
        stem = PurePosixPath(filename or "").stem
        default = stem if re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", stem or "") else "UPLOAD"
        operations, notes = normalize_operations(operations, default)
        rows, parsing = parse_transactions_lenient(operations)
        if not rows:
            raise ValueError(
                "Ни одна строка не прошла проверку"
                + (f": {parsing['errors'][0]['reason']}" if parsing["errors"] else "")
            )
        number = 1 + max((int(r["id"][3:]) for r in existing), default=0)
        dataset_id = f"dbg{number}"
        prefix = f"{dataset_id}-"
        companies = sorted({tx["account_id"] for tx in rows})
        if any(len(prefix + c) > 80 for c in companies):
            raise ValueError("ID компании длиннее 75 символов")
        starts = defaultdict(lambda: "9999-12-31")
        for tx in rows:
            starts[tx["account_id"]] = min(starts[tx["account_id"]], tx["operation_date"])
            tx["account_id"] = prefix + tx["account_id"]
        uploaded = len(rows)
        # A control set may continue companies of the main dataset: their history before the
        # uploaded period gives the model its features. The main accounts stay untouched.
        copied, mismatched = Counter(), []
        if history:
            with self.store.connect() as db:
                known = {
                    r[0]
                    for r in db.execute("SELECT id FROM accounts WHERE owner != ?", (DEBUG_OWNER,))
                }
            first_days, inns = defaultdict(list), defaultdict(set)
            for tx in rows:
                company = tx["account_id"].removeprefix(prefix)
                if tx["operation_date"] == starts[company]:
                    first_days[company].append(tx)
                if tx["counterparty_inn"]:
                    inns[company].add(tx["counterparty_inn"])
            for company in companies:
                if company not in known:
                    continue
                past = [
                    t
                    for t in self.store.transactions(company)
                    if t["operation_date"] < starts[company]
                ]
                # Same id, other company: a set may reuse C001… for new firms. The same firm either
                # continues the balance or pays the same counterparties (a statement may skip days).
                known_inns = {t["counterparty_inn"] for t in past if t["counterparty_inn"]}
                shared = (
                    len(inns[company] & known_inns) / len(inns[company]) if inns[company] else 0
                )
                if past and not (
                    _continues(past[-1], first_days[company]) or shared >= SAME_COUNTERPARTIES
                ):
                    mismatched.append(company)
                    continue
                for tx in past:
                    rows.append(
                        {k: tx[k] for k in HISTORY_FIELDS} | {"account_id": prefix + company}
                    )
                    copied[company] += 1
        if mismatched:
            notes.append(
                "история не добавлена — ни остаток на стыке, ни контрагенты не совпадают с основным "
                f"датасетом, это другие компании с теми же номерами: {', '.join(mismatched[:8])}"
                + (f" и ещё {len(mismatched) - 8}" if len(mismatched) > 8 else "")
            )
        imported = self._import_rows(
            rows, DEBUG_PRINCIPAL, {prefix + c: names.get(c, c) for c in companies}, demo=True
        )
        by_account = defaultdict(list)
        # Statistics of the uploaded set only: the copied history comes after it in `rows`.
        for tx in rows[:uploaded]:
            by_account[tx["account_id"]].append(tx)
        accounts = []
        for account_id in sorted(by_account):
            quality = self.store.quality(account_id)
            txs = by_account[account_id]
            accounts.append(
                {
                    "account_id": account_id,
                    "company_id": account_id.removeprefix(prefix),
                    "data_from": starts[account_id.removeprefix(prefix)],
                    "history_copied": copied[account_id.removeprefix(prefix)],
                    "name": names.get(account_id.removeprefix(prefix), ""),
                    "operations": quality.get("operations", len(txs)),
                    "first_date": quality.get("first_date"),
                    "last_date": quality.get("last_date"),
                    "last_balance_minor": max(
                        txs, key=lambda t: (t["operation_date"], t["operation_id"])
                    )["balance_eod_minor"],
                    "inconsistent_transitions": len(quality.get("inconsistent_transitions", [])),
                    "suspicious_duplicates": len(quality.get("suspicious_duplicates", [])),
                    "invalid_days": quality.get("invalid_days", 0),
                    "unknown_share": _categories(txs)["unknown_share"],
                }
            )
        reports = [self.store.quality(a["account_id"]) for a in accounts]
        report = {
            "filename": PurePosixPath(filename or "dataset").name[:120],
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "source": source,
            "size_bytes": len(payload),
            "rows": parsing["rows"],
            "accepted": uploaded,
            "notes": notes,
            "history_companies": len(copied),
            "history_operations": sum(copied.values()),
            "rejected": parsing["rejected"],
            "errors": parsing["errors"],
            "inserted": imported["inserted"] - sum(copied.values()),
            "duplicates": imported["duplicates"],
            "companies": len(accounts),
            "first_date": min(a["data_from"] for a in accounts),
            "last_date": max(a["last_date"] for a in accounts),
            "quality": _quality_totals(reports),
            "categories": _categories(rows[:uploaded]),
            "accounts": accounts,
        }
        with self.lock, self.store.connect() as db:
            db.execute(
                "INSERT INTO debug_datasets(id, created_at, report) VALUES(?,?,?)",
                (dataset_id, report["created_at"], json.dumps(report, ensure_ascii=False)),
            )
        return {"id": dataset_id, **report}

    def debug_delete(self, dataset_id):
        self.debug_dataset(dataset_id)
        prefix = f"{dataset_id}-"
        with self.lock, self.store.connect() as db:
            # substr, not LIKE: «_» in an id would be a wildcard.
            for table in ACCOUNT_TABLES:
                db.execute(
                    f"DELETE FROM {table} WHERE substr(account_id, 1, ?)=?",
                    (len(prefix), prefix),
                )
            db.execute(
                "DELETE FROM debug_checks WHERE substr(account_id, 1, ?)=?",
                (len(prefix), prefix),
            )
            removed = db.execute(
                "DELETE FROM accounts WHERE substr(id, 1, ?)=? AND owner=?",
                (len(prefix), prefix, DEBUG_OWNER),
            ).rowcount
            db.execute("DELETE FROM debug_datasets WHERE id=?", (dataset_id,))
            db.execute("DELETE FROM debug_evaluations WHERE dataset_id=?", (dataset_id,))
            db.execute("DELETE FROM debug_eval_companies WHERE dataset_id=?", (dataset_id,))
        self.__dict__.get("_eval_jobs", {}).pop(dataset_id, None)
        return {"id": dataset_id, "removed_accounts": removed}

    def debug_check(self, account_id, model="ensemble"):
        """Forecast on the last date plus an honest check: forecast 30 days back, compare to fact."""
        account = self.store.account(account_id)
        if account["owner"] != DEBUG_OWNER:
            raise PermissionError("Проверка доступна только для загруженных на отладку счетов")
        quality = self.store.quality(account_id)
        first = date.fromisoformat(quality["first_date"])
        last = date.fromisoformat(quality["last_date"])
        origin = last - timedelta(days=BACKTEST_DAYS)
        # One model for the forecast and its check: the first in the chain that may run on both
        # dates. On an account closed early that is the weekday baseline (no training, honest).
        model = next(
            (
                m
                for m in self.MODEL_CHAIN.get(model, (model,))
                if self.model_ready(m, last) and self.model_ready(m, origin)
            ),
            "weekday",
        )
        current = self.forecast(ForecastRequest(account_id=account_id, as_of=last, model=model))
        risk = current.risk
        result = {
            "account_id": account_id,
            "as_of": last,
            "model": model,
            "forecast": {
                "run_id": current.run_id,
                "first_gap_date": risk.first_gap_date,
                "liquidity_need_minor": risk.liquidity_need_minor,
                "minimum_balance_minor": risk.minimum_balance_minor,
                "probability_30d": risk.probability_30d,
            },
            "backtest": None,
            "backtest_note": None,
        }
        result["backtest"], result["backtest_note"] = self.account_backtest(
            account_id, first, last, model
        )
        self._save_check(result)
        return result

    def _save_check(self, result):
        # The LLM model review on the debug page reads the checks back from here.
        with self.lock, self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO debug_checks(account_id, payload) VALUES(?,?)",
                (result["account_id"], json.dumps(result, default=str, ensure_ascii=False)),
            )

    def debug_checks(self, dataset_id):
        prefix = f"{dataset_id}-"
        with self.store.connect() as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM debug_checks WHERE substr(account_id, 1, ?)=?",
                    (len(prefix), prefix),
                )
            ]

    def account_backtest(self, account_id, first: date, end: date, model: str):
        """The same model forecast 30 days before `end`, against the balances that followed.

        Returns (backtest, note): only facts up to `end` are used, so it is honest at any date.
        """
        origin = end - timedelta(days=BACKTEST_DAYS)
        if (origin - first).days < MIN_HISTORY_DAYS:
            return None, (
                f"Для проверки нужно {MIN_HISTORY_DAYS} дней истории до "
                f"{origin:%d.%m.%Y}, а их {max(0, (origin - first).days)}"
            )
        try:
            past = self.forecast(ForecastRequest(account_id=account_id, as_of=origin, model=model))
        except ValueError as exc:
            return None, str(exc)[:200]
        daily = self.store.daily(account_id)
        actual = {
            date.fromisoformat(d["date"]): d
            for d in daily
            if origin < date.fromisoformat(d["date"]) <= end
        }
        start = next(
            (
                d["balance_minor"]
                for d in reversed(daily)
                if date.fromisoformat(d["date"]) <= origin
            ),
            past.opening_balance_minor,
        )
        pairs = [
            (p.closing_balance_minor, actual[p.date]["balance_minor"])
            for p in past.points
            if p.date in actual and actual[p.date]["valid"]
        ]
        if not pairs:
            return None, "За 30 дней проверки нет дней с надёжным остатком"
        mae = sum(abs(f - a) for f, a in pairs) // len(pairs)
        mae_constant = sum(abs(start - a) for _, a in pairs) // len(pairs)
        facts = [actual[d]["balance_minor"] for d in sorted(actual) if actual[d]["valid"]]
        actual_gap = next(
            (d for d in sorted(actual) if actual[d]["valid"] and actual[d]["balance_minor"] < 0),
            None,
        )
        return {
            "as_of": origin,
            "days": len(pairs),
            "mae_minor": mae,
            "mae_constant_minor": mae_constant,
            "skill": round(1 - mae / mae_constant, 3) if mae_constant else None,
            "forecast_minimum_minor": past.risk.minimum_balance_minor,
            "actual_minimum_minor": min(facts),
            "forecast_gap_date": past.risk.first_gap_date,
            "actual_gap_date": actual_gap,
        }, None
