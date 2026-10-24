"""Webhooks: outside systems push data into an account automatically.

Each webhook is bound to one account and one kind of data. A sender signs the raw body with
HMAC-SHA256 (`X-Kassandra-Signature: sha256=<hex>`) or passes the secret as a Bearer token.
The data is applied with rights to that account only, a repeated `X-Delivery-Id` is not applied
twice, and every delivery lands in a journal the Sources page shows.
"""

import csv
import hashlib
import hmac
import io
import json
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from cashflow_domain.feeds import BankFeed, ContractSpec, InvoiceSpec, MarketplaceFeed, OfdFeed
from cashflow_services.data import parse_row

MAX_BODY = 20 * 1024 * 1024
SAMPLE_1C = Path(__file__).resolve().parents[2] / "examples" / "1c_statement_sample.txt"
KINDS = {
    "statement": "Банковская выписка: новые операции",
    "1c": "Выписка 1С «Клиент-банк»",
    "bank": "Банк: кредиты, зарплатный проект, платёжки",
    "marketplace": "Маркетплейс: выплаты",
    "ofd": "Онлайн-касса (ОФД): выручка по дням",
    "invoices": "Счета на оплату",
    "contract": "Договор с графиком платежей",
}
STATEMENT_FIELDS = (
    "operation_id",
    "operation_date",
    "amount",
    "direction",
    "counterparty_inn",
    "counterparty_name",
    "payment_purpose",
    "balance_eod",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _error_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        return f"Неверные данные: {where} — {first['msg']}"[:300]
    return str(exc)[:300]


class WebhookMixin:
    # ------------------------------------------------------------------ management

    def webhooks(self, account_id, principal):
        self.authorize(principal, account_id)
        with self.store.connect() as db:
            hooks = [
                dict(r)
                for r in db.execute(
                    "SELECT id, account_id, kind, label, created_at, active FROM webhooks "
                    "WHERE account_id=? ORDER BY created_at",
                    (account_id,),
                )
            ]
            for hook in hooks:
                last = db.execute(
                    "SELECT received_at, status, summary, error FROM webhook_deliveries "
                    "WHERE webhook_id=? ORDER BY id DESC LIMIT 1",
                    (hook["id"],),
                ).fetchone()
                count = db.execute(
                    "SELECT COUNT(*) FROM webhook_deliveries WHERE webhook_id=?", (hook["id"],)
                ).fetchone()[0]
                hook.update(
                    active=bool(hook["active"]),
                    kind_label=KINDS[hook["kind"]],
                    path=f"/api/v1/hooks/{hook['id']}",
                    deliveries=count,
                    last_delivery=None
                    if last is None
                    else {
                        "received_at": last["received_at"],
                        "status": last["status"],
                        "summary": last["summary"],
                        "error": last["error"],
                    },
                )
        return hooks

    def create_webhook(self, account_id, kind, label, principal):
        self.authorize(principal, account_id)
        if kind not in KINDS:
            raise ValueError("Неизвестный тип данных webhook")
        hook_id = "wh_" + secrets.token_hex(8)
        secret = "whsec_" + secrets.token_urlsafe(32)
        with self.lock, self.store.connect() as db:
            if (
                db.execute(
                    "SELECT COUNT(*) FROM webhooks WHERE account_id=?", (account_id,)
                ).fetchone()[0]
                >= 20
            ):
                raise ValueError("У счёта уже 20 webhook: удалите лишние")
            db.execute(
                "INSERT INTO webhooks(id, account_id, kind, label, secret, created_at, created_by, active) "
                "VALUES(?,?,?,?,?,?,?,1)",
                (
                    hook_id,
                    account_id,
                    kind,
                    (label or KINDS[kind])[:120],
                    secret,
                    _now(),
                    principal["id"],
                ),
            )
        hook = next(h for h in self.webhooks(account_id, principal) if h["id"] == hook_id)
        # The secret is shown once: later lists never return it.
        return {**hook, "secret": secret}

    def _webhook(self, hook_id):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM webhooks WHERE id=?", (hook_id,)).fetchone()
        if row is None:
            raise ValueError("Webhook не найден")
        return dict(row)

    def rotate_webhook(self, hook_id, principal):
        hook = self._webhook(hook_id)
        self.authorize(principal, hook["account_id"])
        secret = "whsec_" + secrets.token_urlsafe(32)
        with self.lock, self.store.connect() as db:
            db.execute("UPDATE webhooks SET secret=? WHERE id=?", (secret, hook_id))
        return {"id": hook_id, "secret": secret}

    def delete_webhook(self, hook_id, principal):
        hook = self._webhook(hook_id)
        self.authorize(principal, hook["account_id"])
        with self.lock, self.store.connect() as db:
            db.execute("DELETE FROM webhook_deliveries WHERE webhook_id=?", (hook_id,))
            db.execute("DELETE FROM webhooks WHERE id=?", (hook_id,))
        return {"id": hook_id, "deleted": True}

    def webhook_deliveries(self, account_id, principal, limit=30):
        self.authorize(principal, account_id)
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT d.id, d.webhook_id, w.kind, d.received_at, d.delivery_id, d.status, "
                "d.test, d.summary, d.error FROM webhook_deliveries d "
                "JOIN webhooks w ON w.id = d.webhook_id WHERE w.account_id=? "
                "ORDER BY d.id DESC LIMIT ?",
                (account_id, max(1, min(int(limit), 100))),
            ).fetchall()
        return [{**dict(r), "test": bool(r["test"])} for r in rows]

    # ------------------------------------------------------------------ receiving

    def receive_webhook(self, hook_id, body: bytes, headers: dict, content_type: str = ""):
        """A delivery from outside: verify, apply once, journal. Errors raise after the journal."""
        hook = self._webhook(hook_id)
        if not hook["active"]:
            raise PermissionError("Webhook отключён")
        if not _authentic(hook["secret"], body, headers):
            raise PermissionError("Подпись или ключ webhook не сошлись")
        if len(body) > MAX_BODY:
            raise ValueError("Тело запроса больше 20 МБ")
        delivery_id = (headers.get("x-delivery-id") or "")[:120] or None
        if delivery_id:
            with self.store.connect() as db:
                seen = db.execute(
                    "SELECT status, summary FROM webhook_deliveries WHERE webhook_id=? AND delivery_id=?",
                    (hook_id, delivery_id),
                ).fetchone()
            if seen:
                return {
                    "status": "duplicate",
                    "summary": seen["summary"],
                    "delivery_id": delivery_id,
                }
        return self._deliver(hook, body, content_type, delivery_id, test=False)

    def check_webhook(self, hook_id, principal):
        """The webhook's health from real deliveries only: no sample, nothing written."""
        hook = self._webhook(hook_id)
        self.authorize(principal, hook["account_id"])
        with self.store.connect() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM webhook_deliveries WHERE webhook_id=? AND test=0", (hook_id,)
            ).fetchone()[0]
            last = db.execute(
                "SELECT received_at, status, summary, error FROM webhook_deliveries "
                "WHERE webhook_id=? AND test=0 ORDER BY id DESC LIMIT 1",
                (hook_id,),
            ).fetchone()
        last = dict(last) if last else None
        if not hook["active"]:
            status, summary = "disabled", "Webhook отключён и не принимает данные"
        elif last is None:
            status = "waiting"
            summary = (
                "Webhook включён и ждёт данных. Настоящих доставок ещё не было: отправьте "
                "данные из своей системы на адрес webhook (пример запроса — в окне подключения)."
            )
        elif last["status"] == "error":
            status = "failing"
            summary = f"Последняя доставка {last['received_at']} не принята: {last['error']}"
        else:
            status = "working"
            summary = f"Работает: последняя доставка {last['received_at']} — {last['summary']}"
        return {
            "id": hook_id,
            "status": status,
            "summary": summary,
            "deliveries": count,
            "last_delivery": last,
        }

    def validate_webhook(self, hook_id, principal, body: bytes, content_type: str = ""):
        """Checks the format of a body the user pastes. Nothing is written, nothing journaled."""
        hook = self._webhook(hook_id)
        self.authorize(principal, hook["account_id"])
        if not body.strip():
            raise ValueError("Вставьте тело запроса, которое отправит ваша система")
        principal = {"id": f"webhook:{hook_id}", "accounts": [hook["account_id"]]}
        try:
            summary = self._apply_webhook(hook, body, content_type, principal, dry_run=True)
        except (ValidationError, json.JSONDecodeError) as exc:
            raise ValueError(_error_text(exc)) from exc
        return {"status": "valid", "summary": summary, "delivery_id": None}

    def _deliver(self, hook, body, content_type, delivery_id, test, dry_run=False):
        # Only real deliveries come here: format checks go through validate_webhook.
        # The webhook writes with rights to its own account only, whoever created it.
        principal = {"id": f"webhook:{hook['id']}", "accounts": [hook["account_id"]]}
        try:
            summary = self._apply_webhook(hook, body, content_type, principal, dry_run)
            status, error = "ok", None
        except (ValueError, ValidationError, PermissionError, json.JSONDecodeError) as exc:
            summary, status, error = "", "error", _error_text(exc)
        with self.lock, self.store.connect() as db:
            cursor = db.execute(
                "INSERT INTO webhook_deliveries(webhook_id, received_at, delivery_id, status, test, "
                "summary, error) VALUES(?,?,?,?,?,?,?)",
                (hook["id"], _now(), delivery_id, status, int(test), summary, error),
            )
            record = cursor.lastrowid
        if status == "error":
            raise ValueError(error)
        return {
            "status": status,
            "summary": summary,
            "delivery": record,
            "delivery_id": delivery_id,
        }

    def _apply_webhook(self, hook, body, content_type, principal, dry_run):
        account_id, kind = hook["account_id"], hook["kind"]
        if kind == "statement":
            rows = _statement_rows(body, account_id, content_type)
            if dry_run:
                return f"Формат верный: {len(rows)} операций (проверка, ничего не записано)"
            report = self._import_rows(rows, principal)
            refreshed = len(report.get("auto_refresh") or [])
            return f"Операций новых: {report['inserted']}, повторов: {report['duplicates']}" + (
                f"; прогнозов обновлено: {refreshed}" if refreshed else ""
            )
        if kind == "1c":
            if dry_run:
                from cashflow_services import integrations

                _, info = integrations.parse_client_bank_exchange(body, account_id, None)
                return f"Формат 1С верный: документов {info['documents']} (проверка, ничего не записано)"
            report = self.import_1c(account_id, body, principal)
            return f"1С: операций новых {report['inserted']}, повторов {report['duplicates']}"
        data = json.loads(body.decode("utf-8-sig"))
        if kind in ("bank", "marketplace", "ofd"):
            model = {"bank": BankFeed, "marketplace": MarketplaceFeed, "ofd": OfdFeed}[kind]
            feed = model.model_validate(data)
            if dry_run:
                from cashflow_services import integrations

                build = {
                    "bank": integrations.bank_events,
                    "marketplace": integrations.marketplace_events,
                    "ofd": integrations.ofd_events,
                }[kind]
                known_at = self._known_at(account_id, feed.known_at, feed.base_run_id)
                events = build(feed, account_id, known_at)
                return (
                    f"Формат верный: событий было бы {len(events)} (проверка, ничего не записано)"
                )
            return _register_summary(self.add_feed(account_id, feed, principal, kind))
        if kind == "invoices":
            items = data.get("invoices", data) if isinstance(data, dict) else data
            items = items if isinstance(items, list) else [items]
            specs = TypeAdapter(list[InvoiceSpec]).validate_python(items)
            if dry_run:
                return f"Формат верный: счетов {len(specs)} (проверка, ничего не записано)"
            return _register_summary(self.add_invoices(account_id, specs, principal))
        if kind == "contract":
            spec = ContractSpec.model_validate(data)
            if dry_run:
                return f"Формат верный: договор {spec.contract_id} (проверка, ничего не записано)"
            return _register_summary(self.add_contract(account_id, spec, principal))
        raise ValueError("Неизвестный тип данных webhook")


def _authentic(secret: str, body: bytes, headers: dict) -> bool:
    signature = headers.get("x-kassandra-signature") or ""
    if signature and hmac.compare_digest(signature, sign(secret, body)):
        return True
    auth = headers.get("authorization") or ""
    return auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], secret)


def _statement_rows(body: bytes, account_id: str, content_type: str) -> list[dict]:
    """New statement operations as JSON {"operations": [...]} or the hackathon CSV; every row
    must belong to the webhook's account."""
    text = body.decode("utf-8-sig")
    if text.lstrip().startswith(("{", "[")):
        data = json.loads(text)
        raw = data.get("operations", []) if isinstance(data, dict) else data
        if not isinstance(raw, list):
            raise ValueError("Ожидается список operations")
        source = [
            {k: str(item.get(k, "") if item.get(k) is not None else "") for k in STATEMENT_FIELDS}
            | {"company_id": str(item.get("company_id") or account_id)}
            for item in raw
            if isinstance(item, dict)
        ]
    else:
        reader = csv.DictReader(io.StringIO(text), delimiter=";")
        source = [{**row, "company_id": row.get("company_id") or account_id} for row in reader]
    if not source:
        raise ValueError("В доставке нет операций")
    if len(source) > 20000:
        raise ValueError("Больше 20 000 операций в одной доставке")
    rows = []
    for number, row in enumerate(source, 1):
        if row["company_id"] != account_id:
            raise ValueError(f"Операция {number} относится к другому счёту")
        try:
            rows.append(parse_row(row))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f"Операция {number}: {exc}") from exc
    return rows


def _register_summary(result: dict) -> str:
    parts = [f"событий новых {result.get('created', 0)}"]
    if result.get("revised"):
        parts.append(f"изменено {result['revised']}")
    if result.get("unchanged"):
        parts.append(f"без изменений {result['unchanged']}")
    if result.get("replaced_assumptions"):
        parts.append(f"заменили оценку по истории {len(result['replaced_assumptions'])}")
    return ", ".join(parts).capitalize()


def sample_payload(kind: str, account_id: str, day: date) -> tuple[bytes, str]:
    """What a real sender would push, dated after `day` so it lands in the current forecast."""

    def at(days):
        return str(day + timedelta(days=days))

    stamp = uuid.uuid4().hex[:6]
    if kind == "statement":
        ops = {
            "operations": [
                {
                    "operation_id": f"wh-{stamp}-1",
                    "operation_date": at(1),
                    "amount": "125000.00",
                    "direction": "Поступление",
                    "counterparty_inn": "0012345678",
                    "counterparty_name": "ООО «Пример-клиент»",
                    "payment_purpose": "Оплата по счёту № 77 (пример webhook)",
                    "balance_eod": "0.00",
                }
            ]
        }
        return json.dumps(ops, ensure_ascii=False, indent=1).encode(), "application/json"
    if kind == "1c":
        # The repository sample: an export «Обмен с банком» in Windows-1251.
        return SAMPLE_1C.read_bytes(), "text/plain"
    payload = {
        "bank": {
            "known_at": str(day),
            "loans": [
                {
                    "contract": "КЛ-ПРИМЕР",
                    "lender": "ПАО «Банк»",
                    "schedule": [
                        {"date": at(20), "principal_minor": 5000000, "interest_minor": 750000}
                    ],
                }
            ],
            "payroll": [{"date": at(8), "amount_minor": 20000000, "kind": "salary"}],
            "payment_orders": [],
        },
        "marketplace": {
            "known_at": str(day),
            "marketplace": "Маркетплейс",
            "payouts": [
                {
                    "date": at(5),
                    "amount_minor": 18000000,
                    "report_ref": "Отчёт о реализации (пример)",
                }
            ],
        },
        "ofd": {
            "known_at": str(day),
            "register_id": "ККТ-пример",
            "days": [{"date": str(day), "card_minor": 4500000, "sbp_minor": 1200000}],
        },
        "invoices": {
            "invoices": [
                {
                    "known_at": str(day),
                    "number": f"WH-{stamp}",
                    "direction": "in",
                    "issue_date": str(day),
                    "payment_terms_days": 10,
                    "amount_minor": 9900000,
                    "counterparty_name": "ООО «Пример-клиент»",
                }
            ]
        },
        "contract": {
            "known_at": str(day),
            "contract_id": f"WH-{stamp}",
            "title": "Поставка (пример webhook)",
            "direction": "in",
            "counterparty_name": "ООО «Пример-клиент»",
            "total_minor": 30000000,
            "signed_date": str(day),
            "prepayment_pct": 30,
            "prepayment_days": 3,
            "delivery_date": at(12),
            "deferral_days": 7,
        },
    }[kind]
    return json.dumps(payload, ensure_ascii=False, indent=1).encode(), "application/json"
