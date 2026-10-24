import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, owner TEXT NOT NULL, demo INTEGER NOT NULL DEFAULT 0,
 data_version INTEGER NOT NULL DEFAULT 0, event_version INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS transactions(
 account_id TEXT NOT NULL REFERENCES accounts(id), operation_id TEXT NOT NULL,
 operation_date TEXT NOT NULL, amount_minor INTEGER NOT NULL, direction TEXT NOT NULL,
 counterparty_inn TEXT NOT NULL, counterparty_name TEXT NOT NULL, payment_purpose TEXT NOT NULL,
 balance_eod_minor INTEGER NOT NULL, fingerprint TEXT NOT NULL, category TEXT NOT NULL,
 PRIMARY KEY(account_id, operation_id));
CREATE INDEX IF NOT EXISTS tx_date ON transactions(account_id, operation_date);
CREATE TABLE IF NOT EXISTS category_revisions(
 account_id TEXT NOT NULL, operation_id TEXT NOT NULL, known_at TEXT NOT NULL,
 category TEXT NOT NULL, sequence INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE IF NOT EXISTS daily(
 account_id TEXT NOT NULL, day TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(account_id, day));
CREATE TABLE IF NOT EXISTS quality(account_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS missing_coverage(
 account_id TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
 known_at TEXT NOT NULL,
 PRIMARY KEY(account_id, start_date, end_date));
CREATE TABLE IF NOT EXISTS event_revisions(
 event_id TEXT NOT NULL, account_id TEXT NOT NULL, revision INTEGER NOT NULL,
 known_at TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(event_id, revision));
CREATE INDEX IF NOT EXISTS event_account ON event_revisions(account_id, known_at);
CREATE TABLE IF NOT EXISTS payments(
 event_id TEXT NOT NULL, account_id TEXT NOT NULL, operation_id TEXT NOT NULL,
 amount_minor INTEGER NOT NULL, known_at TEXT NOT NULL,
 PRIMARY KEY(event_id, operation_id));
CREATE TABLE IF NOT EXISTS forecasts(
 run_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS forecast_immutable BEFORE UPDATE ON forecasts
 BEGIN SELECT RAISE(ABORT,'Forecasts are immutable'); END;
CREATE TABLE IF NOT EXISTS scenarios(
 scenario_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS integration_syncs(
 account_id TEXT NOT NULL, provider TEXT NOT NULL, synced_at TEXT NOT NULL, summary TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS sync_account ON integration_syncs(account_id, provider);
CREATE TABLE IF NOT EXISTS counterparty_checks(
 inn TEXT PRIMARY KEY, checked_at TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS debug_datasets(
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, report TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS debug_checks(account_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_reviews(
 run_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, created_at TEXT NOT NULL,
 payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS debug_evaluations(
 dataset_id TEXT PRIMARY KEY, finished_at TEXT NOT NULL, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS debug_eval_companies(
 dataset_id TEXT NOT NULL, account_id TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(dataset_id, account_id));
CREATE TABLE IF NOT EXISTS webhooks(
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL, kind TEXT NOT NULL, label TEXT NOT NULL,
 secret TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS webhook_deliveries(
 id INTEGER PRIMARY KEY AUTOINCREMENT, webhook_id TEXT NOT NULL, received_at TEXT NOT NULL,
 delivery_id TEXT, status TEXT NOT NULL, test INTEGER NOT NULL DEFAULT 0, summary TEXT NOT NULL,
 error TEXT);
CREATE INDEX IF NOT EXISTS webhook_delivery ON webhook_deliveries(webhook_id, delivery_id);
CREATE TABLE IF NOT EXISTS idempotency(
 principal TEXT NOT NULL, scope TEXT NOT NULL, key TEXT NOT NULL, body_hash TEXT NOT NULL,
 payload TEXT NOT NULL, PRIMARY KEY(principal, scope, key));
"""


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            columns = {r[1] for r in db.execute("PRAGMA table_info(missing_coverage)")}
            if "known_at" not in columns:
                db.execute(
                    "ALTER TABLE missing_coverage ADD COLUMN known_at TEXT NOT NULL DEFAULT '0001-01-01'"
                )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def transactions(self, account_id, as_of=None):
        with self.connect() as db:
            query = "SELECT * FROM transactions WHERE account_id=?"
            args = [account_id]
            if as_of is not None:
                query += " AND operation_date<=?"
                args.append(str(as_of))
            result = [
                dict(r) for r in db.execute(query + " ORDER BY operation_date,operation_id", args)
            ]
            revisions = {}
            for row in db.execute(
                "SELECT * FROM category_revisions WHERE account_id=? ORDER BY sequence",
                (account_id,),
            ):
                revisions.setdefault(row["operation_id"], []).append(
                    {"known_at": row["known_at"], "category": row["category"]}
                )
            for tx in result:
                tx["original_category"] = tx["category"]
                tx["category_revisions"] = revisions.get(tx["operation_id"], [])
                for revision in tx["category_revisions"]:
                    if as_of is None or revision["known_at"] <= str(as_of):
                        tx["category"] = revision["category"]
            return result

    def daily(self, account_id):
        with self.connect() as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM daily WHERE account_id=? ORDER BY day", (account_id,)
                )
            ]

    def quality(self, account_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM quality WHERE account_id=?", (account_id,)
            ).fetchone()
            return json.loads(row[0]) if row else {}

    def account(self, account_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
            if not row:
                raise ValueError("Счёт не найден")
            return dict(row)
