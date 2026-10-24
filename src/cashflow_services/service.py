import csv
import hashlib
import io
import json
import os
import threading
import uuid
from collections import defaultdict
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from cashflow_domain.engine import (
    assemble,
    deterministic_risk,
    fixed_cost_share,
    flow_shares,
    net_known_events,
    scenario,
)
from cashflow_domain.explain import explain_gap
from cashflow_domain.schemas import (
    ASSUMPTION_SOURCES,
    EventCreate,
    ForecastRequest,
    ForecastResult,
    PlannedEvent,
    ScenarioResult,
    is_revenue,
)
from cashflow_ml import cold_start, structural
from cashflow_ml.calibration import add_calibrated_uncertainty
from cashflow_ml.models import ModelRegistry, predict
from cashflow_ml.regularity import component_key, predicted_events
from cashflow_ml.risk import add_uncertainty
from cashflow_services.data import daily_calendar, parse_transactions
from cashflow_services.debug import DebugMixin
from cashflow_services.editing import EditingMixin
from cashflow_services.evaluation_job import EvaluationMixin
from cashflow_services.sources import SourcesMixin
from cashflow_services.store import Store
from cashflow_services.webhooks import WebhookMixin


class ConflictError(ValueError):
    pass


class Service(SourcesMixin, EditingMixin, DebugMixin, WebhookMixin, EvaluationMixin):
    def __init__(self, db_path="data/cashflow.sqlite", artifacts="artifacts"):
        self.store = Store(db_path)
        self.registry = ModelRegistry(Path(artifacts) / "models")
        self.artifacts = Path(artifacts)
        self.lock = threading.RLock()

    def allowed(self, principal: dict, account_id: str) -> bool:
        try:
            account = self.store.account(account_id)
        except ValueError:
            return False
        if principal["id"] == "demo":
            return bool(account["demo"])
        return (
            account["owner"] == principal["id"]
            or account_id in principal["accounts"]
            or "*" in principal["accounts"]
        )

    def authorize(self, principal, account_id):
        if not self.allowed(principal, account_id):
            raise PermissionError("Нет доступа к счёту")

    def accounts(self, principal):
        with self.store.connect() as db:
            accounts = [dict(r) for r in db.execute("SELECT * FROM accounts ORDER BY id")]
        return [
            {**a, "quality": self.store.quality(a["id"])}
            for a in accounts
            if self.allowed(principal, a["id"])
        ]

    def import_transactions(self, payload: bytes, principal, names=None, demo=False):
        if principal["id"] == "demo":
            raise PermissionError("Для загрузки своей выписки настройте личный доступ по README")
        return self._import_rows(parse_transactions(payload), principal, names, demo)

    def _import_rows(self, rows, principal, names=None, demo=False):
        grouped = defaultdict(list)
        for row in rows:
            grouped[row["account_id"]].append(row)
        inserted, duplicates, touched = 0, 0, []
        with self.lock, self.store.connect() as db:
            for account_id, txs in grouped.items():
                existing = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
                if existing:
                    self.authorize(principal, account_id)
                else:
                    db.execute(
                        "INSERT INTO accounts(id,name,owner,demo) VALUES(?,?,?,?)",
                        (
                            account_id,
                            (names or {}).get(account_id, account_id),
                            principal["id"],
                            int(demo),
                        ),
                    )
                count = 0
                for row in txs:
                    old = db.execute(
                        "SELECT fingerprint FROM transactions WHERE account_id=? AND operation_id=?",
                        (account_id, row["operation_id"]),
                    ).fetchone()
                    if old:
                        if old[0] != row["fingerprint"]:
                            raise ConflictError(
                                "ID операции уже импортирован с другим содержимым; пакет отклонён"
                            )
                        duplicates += 1
                        continue
                    columns = list(row)
                    db.execute(
                        f"INSERT INTO transactions({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                        list(row.values()),
                    )
                    inserted += 1
                    count += 1
                if count:
                    db.execute(
                        "UPDATE accounts SET data_version=data_version+1 WHERE id=?", (account_id,)
                    )
                    all_txs = [
                        dict(r)
                        for r in db.execute(
                            "SELECT * FROM transactions WHERE account_id=? ORDER BY operation_date,operation_id",
                            (account_id,),
                        )
                    ]
                    missing = [
                        dict(r)
                        for r in db.execute(
                            "SELECT * FROM missing_coverage WHERE account_id=?", (account_id,)
                        )
                    ]
                    daily, quality = daily_calendar(all_txs, missing)
                    # Only this account's aggregates are refreshed; the archive is not re-read.
                    old_daily = {
                        r["day"]: r["payload"]
                        for r in db.execute(
                            "SELECT day,payload FROM daily WHERE account_id=?", (account_id,)
                        )
                    }
                    db.executemany(
                        "INSERT INTO daily VALUES(?,?,?) ON CONFLICT(account_id,day) DO UPDATE SET payload=excluded.payload",
                        [
                            (account_id, d["date"], json.dumps(d))
                            for d in daily
                            if old_daily.get(d["date"]) != json.dumps(d)
                        ],
                    )
                    db.execute(
                        "INSERT OR REPLACE INTO quality VALUES(?,?)",
                        (account_id, json.dumps(quality)),
                    )
                    touched.append(account_id)
        # New operations may pay registered events: link them before the forecast is refreshed.
        reconciliation = {}
        for account_id in touched:
            result = self.reconcile(account_id, principal)
            if result["checked_events"]:
                reconciliation[account_id] = {
                    "linked": len(result["linked"]),
                    "needs_review": len(result["needs_review"]),
                }
        return {
            "inserted": inserted,
            "duplicates": duplicates,
            "touched_accounts": touched,
            "accounts": list(grouped),
            "reconciliation": reconciliation,
            "auto_refresh": self.refresh_latest(touched) if touched else [],
        }

    def refresh_latest(self, accounts):
        results = []
        # One new version for each affected account with an existing saved forecast, no fit.
        for account_id in accounts:
            with self.store.connect() as db:
                old = db.execute(
                    "SELECT payload FROM forecasts WHERE account_id=? ORDER BY rowid DESC LIMIT 1",
                    (account_id,),
                ).fetchone()
            if not old:
                continue
            base = ForecastResult.model_validate_json(old[0])
            as_of = date.fromisoformat(self.store.daily(account_id)[-1]["date"])
            try:
                result = self.forecast(
                    ForecastRequest(
                        account_id=account_id,
                        as_of=as_of,
                        model=base.model,
                        calendar=base.calendar,
                        reserve_minor=base.reserve_minor,
                        credit_limit_minor=base.credit_limit_minor,
                    )
                )
                results.append(
                    {"account_id": account_id, "run_id": result.run_id, "status": "created"}
                )
            except ValueError as exc:
                results.append(
                    {"account_id": account_id, "status": "needs_review", "reason": str(exc)}
                )
        return results

    def events(self, account_id, as_of):
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT event_id,revision,payload FROM event_revisions WHERE account_id=? AND known_at<=? ORDER BY revision",
                (account_id, str(as_of)),
            ).fetchall()
            latest = {}
            for row in rows:
                latest[row["event_id"]] = PlannedEvent.model_validate_json(row["payload"])
            payments = db.execute(
                "SELECT * FROM payments WHERE account_id=? AND known_at<=?",
                (account_id, str(as_of)),
            ).fetchall()
            for row in payments:
                event = latest.get(row["event_id"])
                # A payment affects a historical slice only after BOTH booking and knowledge.
                tx = db.execute(
                    "SELECT operation_date FROM transactions WHERE account_id=? AND operation_id=?",
                    (account_id, row["operation_id"]),
                ).fetchone()
                if event and tx and tx[0] <= str(as_of):
                    event.paid_minor += row["amount_minor"]
            for event in latest.values():
                # A cancelled remainder stays cancelled even when part of it was paid.
                if event.paid_minor and event.status != "cancelled":
                    event.status = (
                        "paid" if event.paid_minor == event.amount_minor else "partially_paid"
                    )
        return list(latest.values())

    def add_event(self, request: EventCreate, principal, connection=None):
        event = request.event
        self.authorize(principal, event.account_id)
        base = None
        if request.base_run_id:
            base = self.get_forecast(request.base_run_id)
            self.authorize(principal, base.account_id)
            if base.account_id != event.account_id:
                raise ValueError("Базовый прогноз относится к другому счёту")
        if event.source in ASSUMPTION_SOURCES or event.status != "confirmed" or event.paid_minor:
            raise ValueError(
                "Реестр принимает подтверждённые неоплаченные события; оплата связывается с операцией"
            )
        if event.decision == "replace":
            if not request.base_run_id:
                raise ValueError("Для замещения нужен базовый прогноз")
            target = next(
                (
                    e
                    for e in base.events
                    if e.id == event.replacement_id and e.source in ASSUMPTION_SOURCES
                ),
                None,
            )
            if base.account_id != event.account_id or not target:
                raise ValueError("Замещаемое предположение не найдено")
            if target.component_id != event.component_id or target.direction != event.direction:
                raise ValueError("Компонент и направление должны совпадать с предположением")
            if event.known_at < base.as_of:
                raise ValueError("Нельзя замещать предположение, ещё не известное на known_at")
        with (
            self.lock,
            nullcontext(connection) if connection is not None else self.store.connect() as db,
        ):
            old = db.execute(
                "SELECT payload FROM event_revisions WHERE event_id=?", (event.id,)
            ).fetchone()
            if old:
                if PlannedEvent.model_validate_json(old[0]) == event:
                    return event
                raise ConflictError("ID события уже используется; выберите новый ID")
            if event.replacement_id:
                for row in db.execute(
                    "SELECT payload FROM event_revisions WHERE account_id=?", (event.account_id,)
                ):
                    other = PlannedEvent.model_validate_json(row[0])
                    if other.replacement_id == event.replacement_id:
                        raise ConflictError("Предположение уже замещено")
            db.execute(
                "INSERT INTO event_revisions VALUES(?,?,?,?,?)",
                (event.id, event.account_id, 1, str(event.known_at), event.model_dump_json()),
            )
            db.execute(
                "UPDATE accounts SET event_version=event_version+1 WHERE id=?", (event.account_id,)
            )
        return event

    def import_events(self, payload, principal, base_run_id=None):
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")), delimiter=";")
        parsed = []
        for row in reader:
            for key in ("amount_minor", "paid_minor"):
                if row.get(key):
                    row[key] = int(row[key])
            row = {k: v for k, v in row.items() if v != ""}
            row["source"] = "csv"
            parsed.append(
                EventCreate(event=PlannedEvent.model_validate(row), base_run_id=base_run_id)
            )
        if not parsed:
            raise ValueError("Реестр пуст")
        # Validate all access before the first mutation.
        for request in parsed:
            self.authorize(principal, request.event.account_id)
        with self.lock, self.store.connect() as db:
            return [
                self.add_event(r, principal, connection=db).model_dump(mode="json") for r in parsed
            ]

    def link_payment(self, event_id, payment, principal):
        with self.lock, self.store.connect() as db:
            row = db.execute(
                "SELECT account_id,payload FROM event_revisions WHERE event_id=? ORDER BY revision DESC LIMIT 1",
                (event_id,),
            ).fetchone()
            if not row:
                raise ValueError("Событие не найдено")
            self.authorize(principal, row["account_id"])
            event = PlannedEvent.model_validate_json(row["payload"])
            tx = db.execute(
                "SELECT * FROM transactions WHERE account_id=? AND operation_id=?",
                (event.account_id, payment.operation_id),
            ).fetchone()
            if not tx or tx["direction"] != event.direction:
                raise ValueError("Нет операции соответствующего счёта и направления")
            if str(payment.known_at) < max(tx["operation_date"], str(event.known_at)):
                raise ValueError("Дата знания раньше операции или события")
            old = db.execute(
                "SELECT amount_minor,known_at FROM payments WHERE event_id=? AND operation_id=?",
                (event_id, payment.operation_id),
            ).fetchone()
            if old:
                if old[0] == payment.amount_minor and old[1] == str(payment.known_at):
                    return {"status": "already_linked"}
                raise ConflictError("Связь оплаты уже существует")
            used_tx = db.execute(
                "SELECT COALESCE(SUM(amount_minor),0) FROM payments WHERE account_id=? AND operation_id=?",
                (event.account_id, payment.operation_id),
            ).fetchone()[0]
            used_event = db.execute(
                "SELECT COALESCE(SUM(amount_minor),0) FROM payments WHERE event_id=?", (event_id,)
            ).fetchone()[0]
            if (
                used_tx + payment.amount_minor > tx["amount_minor"]
                or used_event + payment.amount_minor > event.amount_minor
            ):
                raise ValueError("Оплата превышает доступный остаток операции или события")
            db.execute(
                "INSERT INTO payments VALUES(?,?,?,?,?)",
                (
                    event_id,
                    event.account_id,
                    payment.operation_id,
                    payment.amount_minor,
                    str(payment.known_at),
                ),
            )
            db.execute(
                "UPDATE accounts SET event_version=event_version+1 WHERE id=?", (event.account_id,)
            )
        return {
            "status": "linked",
            "remaining_minor": event.amount_minor - used_event - payment.amount_minor,
        }

    def forecast(self, request: ForecastRequest):
        with self.lock:
            return self._forecast(self._usable(request))

    MODEL_CHAIN = {"ensemble": ("ensemble", "hgb", "weekday"), "hgb": ("hgb", "weekday")}

    def model_ready(self, model: str, as_of: date, calendar: bool = True) -> bool:
        """Ensemble weights start on 2026-02-28 and hgb is trained to 2025-12-31: earlier dates
        would see their own future, so these models are not offered there."""
        try:
            if model == "ensemble":
                from cashflow_ml import ensemble

                return ensemble.available(ensemble.load(self.artifacts), as_of)
            if model == "hgb":
                self.registry.select(as_of, calendar)
        except ValueError:
            return False
        return True

    def _usable(self, request: ForecastRequest) -> ForecastRequest:
        """The requested model, or the next honest one when it cannot run on this date.
        The result carries the model that actually ran."""
        chain = self.MODEL_CHAIN.get(request.model)
        if not chain or request.model_version:
            return request
        for model in chain:
            if self.model_ready(model, request.as_of, request.calendar):
                return (
                    request
                    if model == request.model
                    else request.model_copy(update={"model": model})
                )
        return request

    def mark_missing(self, account_id, start, end, known_at, principal):
        self.authorize(principal, account_id)
        if start > end or known_at < end:
            raise ValueError("Проверьте интервал пропуска и дату его обнаружения")
        with self.lock, self.store.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO missing_coverage VALUES(?,?,?,?)",
                (account_id, str(start), str(end), str(known_at)),
            )
            missing = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM missing_coverage WHERE account_id=?", (account_id,)
                )
            ]
            txs = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM transactions WHERE account_id=? ORDER BY operation_date,operation_id",
                    (account_id,),
                )
            ]
            daily, quality = daily_calendar(txs, missing)
            db.execute("DELETE FROM daily WHERE account_id=?", (account_id,))
            db.executemany(
                "INSERT INTO daily VALUES(?,?,?)",
                [(account_id, d["date"], json.dumps(d)) for d in daily],
            )
            db.execute(
                "INSERT OR REPLACE INTO quality VALUES(?,?)", (account_id, json.dumps(quality))
            )
            db.execute("UPDATE accounts SET data_version=data_version+1 WHERE id=?", (account_id,))
        return {"status": "confirmed_missing", "start_date": str(start), "end_date": str(end)}

    def _forecast(self, request):
        account = self.store.account(request.account_id)
        if request.data_version is not None and request.data_version != account["data_version"]:
            raise ConflictError("Версия данных изменилась")
        if request.event_version is not None and request.event_version != account["event_version"]:
            raise ConflictError("Версия событий изменилась")
        # Rebuild only from the cut: future reconciliation flags cannot leak backwards.
        txs = self.store.transactions(request.account_id, request.as_of)
        if not txs:
            raise ValueError("Недостаточно истории на выбранную дату")
        with self.store.connect() as db:
            missing = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM missing_coverage WHERE account_id=? AND known_at<=?",
                    (request.account_id, str(request.as_of)),
                )
            ]
        daily, quality = daily_calendar(txs, missing)
        latest = self.store.daily(request.account_id)[-1]["date"]
        if str(request.as_of) > latest:
            raise ValueError("as_of за пределами доступной выписки; загрузите новые данные")
        last = daily[-1]
        while last["date"] < str(request.as_of):
            last = {
                **last,
                "date": str(date.fromisoformat(last["date"]) + timedelta(days=1)),
                "inflow_minor": 0,
                "outflow_minor": 0,
                "operation_count": 0,
                "coverage": "no_operations_unconfirmed",
            }
            daily.append(last)
        structural_model = request.model == "structural"
        if structural_model:
            self._prepare_structural()
        values, components, scale, version, trained_to = predict(
            txs,
            request.as_of,
            request.model,
            request.calendar,
            self.registry,
            request.model_version,
            account_id=request.account_id,
            legal_form=structural.legal_form_from_name(account["name"]),
            docs=request.retro_registry,
            daily=daily,
        )
        if request.model == "constant":
            components = {}
        if structural_model:
            # События механизмов, налогового календаря и реестра документов уже с источником.
            events = list(components.events)
        else:
            events = predicted_events(components, request.account_id, request.as_of)
            self._tag_counterparties(events, txs)
        registered = self.events(request.account_id, request.as_of)
        replaced_ids = {e.replacement_id for e in registered if e.decision == "replace"}
        # Suppress an already replaced monthly occurrence even if recomputed after its date.
        replaced_component_dates = {
            (e.component_id, e.replacement_id.rsplit(":", 1)[-1])
            for e in registered
            if e.replacement_id
        }
        events = [
            e
            for e in events
            if e.id not in replaced_ids
            and (e.component_id, str(e.expected_date)) not in replaced_component_dates
        ]
        flags = []
        if len(daily) < 30:
            flags.append("Короткая история: меньше 30 календарных дней")
        if quality["inconsistent_transitions"]:
            flags.append(
                f"Несогласованные переходы в доступной истории: {len(quality['inconsistent_transitions'])}; пропуски не исправлены"
            )
        flags.append(
            "Дни без операций внутри покрытия считаются нулевым потоком по допущению; это не подтверждение полноты выписки"
        )
        if missing:
            flags.append(
                f"Подтверждённые пропуски выписки: {len(missing)}; неизвестные потоки не восстановлены"
            )
        if request.model == "constant" and any(
            e.decision == "replace" and e.status != "cancelled" for e in registered
        ):
            raise ValueError(
                "Постоянный остаток не содержит ожиданий для замещения; выберите прогноз с календарём"
            )
        for e in registered:
            if e.status == "cancelled":
                # A skipped expectation: it only hides the history-based event.
                continue
            if e.decision == "replace" and not request.calendar and not structural_model:
                raise ValueError("Реестр замещает календарное ожидание: включите календарь")
            if (
                e.decision == "replace"
                and e.component_id not in components
                and e.remaining_minor
                and e.expected_date > request.as_of
            ):
                # Its old component would otherwise remain inside the full residual model.
                raise ValueError(
                    "Замещённый компонент больше не распознан; требуется проверить разложение перед прогнозом"
                )
            if e.expected_date <= request.as_of and e.remaining_minor and e.status != "cancelled":
                flags.append(
                    f"Просрочено событие {e.id}: остаток {e.remaining_minor} коп.; требуется новая ожидаемая дата"
                )
            if e.decision == "additional":
                flags.append(f"Событие {e.id}: пользователь выбрал дополнительное движение")
        if structural_model:
            flags.append(
                "Структурная модель: механизмы и налоги определены по истории до даты расчёта; "
                + (
                    "БЭКТЕСТ С УТЕЧКОЙ: реестр документов восстановлен по оплатам после даты "
                    "расчёта (ретро-режим D6), оценка оптимистична и недостижима в реальной работе"
                    if components.meta.get("documents_registry")
                    else "реестр документов не используется, документы в остатке (как в "
                    "реальной работе на правом краю данных)"
                )
            )
        confidence = cold_start.assess(cold_start.history_days(txs, request.as_of)).to_dict()
        if confidence["level"] in ("none", "very_low", "low"):
            flags.append(f"Уровень доверия: {confidence['label']}. {confidence['limitation']}")
        if confidence["refuse"]:
            flags.append("Истории меньше 7 дней: прогноз не рекомендуется использовать")
        registered, overdue = self._overdue_policy(registered, request.as_of)
        for item in overdue:
            if item["treatment"] == "rolled_forward":
                flags.append(
                    f"Просроченное обязательство {item['event_id']} перенесено на {item['new_date']}: "
                    "неоплаченный долг не исчезает из прогноза"
                )
        events += registered
        # Known events from integrations replace their share of the model's average flows.
        values, netting = net_known_events(
            values, registered, request.as_of, flow_shares(txs, request.as_of)
        )
        if netting:
            flags.append(
                f"Подтверждённые события заменили {netting['displaced_minor']} коп. среднего потока "
                "модели, чтобы одни и те же деньги не учитывались дважды"
            )
        points = assemble(request.as_of, daily[-1]["balance_minor"], values, events)
        risk = deterministic_risk(
            daily[-1]["balance_minor"], points, request.reserve_minor, request.credit_limit_minor
        )
        interval = (
            components.meta.get("cold_start", {}).get("interval") if structural_model else None
        )
        if interval:
            low, high = cold_start.interval_bounds(
                [p.closing_balance_minor for p in points], interval["half_width_minor"]
            )
            for point, lo, hi in zip(points, low, high, strict=True):
                point.p10_minor, point.p90_minor = int(lo), int(hi)
        recipe = f"{request.model}|{version}|calendar={request.calendar}"
        if len(daily) >= 90 and all(d["valid"] for d in daily[-90:]):
            risk = add_uncertainty(
                risk,
                points,
                request.as_of,
                recipe,
                scale,
                self.artifacts,
                bool(registered),
                events=events,
                docs=bool(structural_model and components.meta.get("documents_registry")),
            )
        else:
            risk.limitation = "Короткая или несогласованная последняя история: перенос банка ошибок не обоснован. Вероятность недоступна; отсутствие точечного разрыва не означает отсутствия риска."
        risk.stress = self._stress(
            request.as_of,
            daily[-1]["balance_minor"],
            values,
            events,
            request.reserve_minor,
            request.credit_limit_minor,
        )
        result = ForecastResult(
            run_id=str(uuid.uuid4()),
            account_id=request.account_id,
            as_of=request.as_of,
            model=request.model,
            model_version=version,
            trained_to=trained_to,
            data_version=account["data_version"],
            event_version=account["event_version"],
            calendar=request.calendar,
            opening_balance_minor=daily[-1]["balance_minor"],
            reserve_minor=request.reserve_minor,
            credit_limit_minor=request.credit_limit_minor,
            points=points,
            events=events,
            risk=risk,
            quality_flags=flags,
            confidence=confidence,
            latest_operation_date=date.fromisoformat(latest),
            history=daily[-60:],
            created_at=datetime.now(UTC).isoformat(),
            netting=netting,
            overdue_events=overdue,
        )
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO forecasts VALUES(?,?,?)",
                (result.run_id, result.account_id, result.model_dump_json()),
            )
        return result

    def _prepare_structural(self):
        """Общий реестр документов по архиву; пересобирается при смене версии данных."""
        with self.store.connect() as db:
            signature = tuple(
                (r["id"], r["data_version"], r["name"])
                for r in db.execute("SELECT id,data_version,name FROM accounts ORDER BY id")
            )
        if getattr(self, "_structural_signature", None) == signature:
            return
        info = structural.load_company_info(
            os.getenv("CASHFLOW_COMPANIES", "dataset/companies.csv")
        )
        for account_id, _, name in signature:
            info.setdefault(account_id, {"legal_form": structural.legal_form_from_name(name)})
        data = {a: (self.store.transactions(a), []) for a, _, _ in signature}
        industries = {a: v.get("industry", "") for a, v in info.items()}
        structural.configure(structural.build_universe(data, industries), info)
        cold_start.configure(data, industries)
        self._structural_signature = signature

    def get_forecast(self, run_id):
        with self.store.connect() as db:
            row = db.execute("SELECT payload FROM forecasts WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("Прогноз не найден")
        return ForecastResult.model_validate_json(row[0])

    @staticmethod
    def _tag_counterparties(events, txs):
        """Regular events know their component only; attach the counterparty seen in history."""
        wanted = {e.component_id for e in events if not e.counterparty_inn}
        found = {}
        for tx in reversed(txs):
            if not wanted:
                break
            key = component_key(tx)
            if key in wanted:
                found[key] = (tx["counterparty_inn"] or None, tx["counterparty_name"] or None)
                wanted.discard(key)
        for e in events:
            if e.component_id in found:
                e.counterparty_inn, e.counterparty_name = found[e.component_id]

    def evaluate_scenario(self, spec):
        base = self.get_forecast(spec.base_run_id)
        parts = spec.parts if spec.kind == "combined" else [spec]
        context = {}
        fixed = [p for p in parts if p.kind == "fixed_cost_increase"]
        if fixed:
            # History only up to as_of: the share never uses later operations.
            txs = self.store.transactions(base.account_id, base.as_of)
            context["fixed_share"] = fixed_cost_share(
                txs,
                base.as_of,
                [c.value for c in fixed[0].categories] if fixed[0].categories else None,
            )
        result = scenario(base, spec, str(uuid.uuid4()), context)
        if base.model == "structural" and base.risk.probability_30d is not None:
            # Та же симуляция на сценарной траектории; калибровка на сценариях не проверялась.
            txs = self.store.transactions(base.account_id, base.as_of)
            add_calibrated_uncertainty(
                result.risk,
                result.points,
                result.events,
                base.as_of,
                structural.history_scale(txs, base.as_of),
                self.artifacts,
                structural.docs_available(base.account_id, base.as_of),
                scenario=True,
            )
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO scenarios VALUES(?,?,?)",
                (result.scenario_id, base.account_id, result.model_dump_json()),
            )
        return result

    def top_clients(self, account_id, as_of, limit=10, days=90, run_id=None):
        """Clients by operating-revenue receipts over the `days` up to and including as_of."""
        since = str(as_of - timedelta(days=days))
        totals = {}
        for tx in self.store.transactions(account_id, as_of):
            if tx["operation_date"] <= since or not is_revenue(tx["category"], tx["direction"]):
                continue
            key = tx["counterparty_inn"] or tx["counterparty_name"]
            row = totals.setdefault(
                key,
                {
                    "counterparty_inn": tx["counterparty_inn"] or None,
                    "counterparty_name": tx["counterparty_name"] or None,
                    "amount_minor": 0,
                    "operations": 0,
                    "last_date": tx["operation_date"],
                },
            )
            row["amount_minor"] += tx["amount_minor"]
            row["operations"] += 1
            row["last_date"] = max(row["last_date"], tx["operation_date"])
        total = sum(r["amount_minor"] for r in totals.values())
        rows = sorted(
            totals.values(), key=lambda r: (-r["amount_minor"], r["counterparty_inn"] or "")
        )
        rows = rows[:limit]
        upcoming = {}
        if run_id:
            base = self.get_forecast(run_id)
            if base.account_id != account_id:
                raise ValueError("Прогноз относится к другому счёту")
            for e in base.events:
                if (
                    e.direction == "in"
                    and e.expected_date > base.as_of
                    and e.status
                    not in {
                        "paid",
                        "cancelled",
                        "review",
                    }
                ):
                    # The INN identifies a client; the name is used only when there is none.
                    key = e.counterparty_inn or e.counterparty_name
                    if key:
                        upcoming.setdefault(key.casefold(), {})[e.id] = e.remaining_minor
        for r in rows:
            r["share"] = round(r["amount_minor"] / total, 4) if total else 0.0
            if run_id:
                key = r["counterparty_inn"] or r["counterparty_name"] or ""
                ids = upcoming.get(key.casefold(), {})
                r["upcoming_events"] = len(ids)
                r["upcoming_minor"] = sum(ids.values())
        return rows

    def explain(self, *, run_id=None, scenario_id=None):
        if scenario_id:
            result = self.get_scenario(scenario_id)
            base = self.get_forecast(result.base_run_id)
            return explain_gap(
                base, result.points, result.events, result.risk, result.assumptions[1:]
            )
        base = self.get_forecast(run_id)
        return explain_gap(base, base.points, base.events, base.risk)

    def get_scenario(self, scenario_id):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM scenarios WHERE scenario_id=?", (scenario_id,)
            ).fetchone()
        if not row:
            raise ValueError("Сценарий не найден")
        return ScenarioResult.model_validate_json(row[0])

    def plan_fact(self, run_id):
        base = self.get_forecast(run_id)
        actual = {d["date"]: d for d in self.store.daily(base.account_id)}
        rows = []
        for point in base.points:
            d = actual.get(str(point.date))
            value = d["balance_minor"] if d and d["valid"] else None
            rows.append(
                {
                    "date": str(point.date),
                    "planned_minor": point.closing_balance_minor,
                    "actual_minor": value,
                    "delta_minor": value - point.closing_balance_minor
                    if value is not None
                    else None,
                    "planned_in_minor": point.event_in_minor + point.residual_in_minor,
                    "planned_out_minor": point.event_out_minor + point.residual_out_minor,
                    "actual_in_minor": d["inflow_minor"] if d and d["valid"] else None,
                    "actual_out_minor": d["outflow_minor"] if d and d["valid"] else None,
                    "coverage": d["coverage"] if d else "outside_coverage",
                }
            )
        events = self.events(
            base.account_id, date.fromisoformat(self.store.daily(base.account_id)[-1]["date"])
        )
        suggestions = []
        for e in events:
            candidates = [
                t["operation_id"]
                for t in self.store.transactions(base.account_id)
                if t["direction"] == e.direction
                and (
                    t["category"] == e.category
                    or (is_revenue(t["category"], "in") and is_revenue(e.category, "in"))
                )
                and abs((date.fromisoformat(t["operation_date"]) - e.expected_date).days) <= 5
            ]
            if e.remaining_minor and candidates:
                suggestions.append(
                    {
                        "event_id": e.id,
                        "candidate_operation_ids": candidates,
                        "status": "needs_review",
                        "reason": "Совпадение категории/даты не доказывает оплату",
                    }
                )
        return {
            "run_id": run_id,
            "data_version": self.store.account(base.account_id)["data_version"],
            "rows": rows,
            "matching_review": suggestions,
        }

    def idempotent(self, principal, scope, key, body, calculate):
        if not key:
            return calculate()
        if len(key) > 160:
            raise ValueError("Слишком длинный Idempotency-Key")
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        with self.lock:
            with self.store.connect() as db:
                old = db.execute(
                    "SELECT body_hash,payload FROM idempotency WHERE principal=? AND scope=? AND key=?",
                    (principal["id"], scope, key),
                ).fetchone()
            if old:
                if old[0] != digest:
                    raise ConflictError("Idempotency-Key использован с другим телом запроса")
                return json.loads(old[1])
            result = calculate()
            payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
            with self.store.connect() as db:
                db.execute(
                    "INSERT INTO idempotency VALUES(?,?,?,?,?)",
                    (principal["id"], scope, key, digest, json.dumps(payload)),
                )
            return payload
