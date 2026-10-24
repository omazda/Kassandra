"""Data sources beyond the statement and the API views built on them (Service mixin).

Contracts, invoices, taxes, bank feeds, marketplaces, cash registers and 1С all end up as
PlannedEvent records in the same versioned register, so the forecast, scenarios and plan-fact see
them without any special case, and every event keeps a visible `source`.
"""

import json
import re
import time
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from cashflow_domain.engine import assemble, deterministic_risk, flow_shares, net_known_events
from cashflow_domain.explain import fmt_day, fmt_money
from cashflow_domain.feeds import InvoiceSpec
from cashflow_domain.schemas import (
    ASSUMPTION_SOURCES,
    CATEGORY_LABELS,
    CERTAINTY_LABELS,
    FINANCING_CATEGORIES,
    FIXED_COST_CATEGORIES,
    REVENUE_CATEGORIES,
    SOURCE_LABELS,
    SOURCE_SHORT_LABELS,
    Category,
    EventCreate,
    ForecastRequest,
    ForecastResult,
    PaymentLink,
    PlannedEvent,
    is_revenue,
)
from cashflow_ml import structural
from cashflow_ml.calendar_ru import next_working_day
from cashflow_ml.features import prepare
from cashflow_ml.regularity import detect_components, predicted_events
from cashflow_ml.risk import add_uncertainty
from cashflow_ml.tax_calendar import REGIME_LABELS, tax_events
from cashflow_services import integrations

# Assumption and confirmed event of the same flow: categories that identify it without a party.
SAME_FLOW_CATEGORIES = frozenset({"salary", "tax", "debt_service", *FIXED_COST_CATEGORIES})
TAX_RULES = {
    "common": "ЕНП — до 28-го числа каждого месяца: НДФЛ и страховые взносы за прошлый месяц.",
    "usn_6": "УСН «доходы»: авансы до 28 апреля, 28 июля и 28 октября; налог за год — до 28 марта (ООО) или 28 апреля (ИП).",
    "usn_15": "УСН «доходы минус расходы»: авансы до 28 апреля, 28 июля и 28 октября; налог за год — до 28 марта (ООО) или 28 апреля (ИП).",
    "osno": "ОСНО: НДС за квартал — тремя равными частями до 28-го числа каждого из трёх следующих месяцев; авансы по налогу на прибыль — до 28-го.",
    "esxn": "ЕСХН: аванс за полугодие — до 28 июля, налог за год — до 28 марта.",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def category_group(code: str) -> str:
    if code in REVENUE_CATEGORIES:
        return "revenue"
    if code in FINANCING_CATEGORIES or code in ("owner_withdrawal", "debt_service"):
        return "financing"
    if code in ("tax",):
        return "tax"
    if code in ("salary",):
        return "payroll"
    if code in FIXED_COST_CATEGORIES:
        return "fixed"
    if code in ("supplier", "contractor", "logistics", "marketing", "operating_expense"):
        return "operations"
    if code == "unknown":
        return "unknown"
    return "other"


GROUP_LABELS = {
    "revenue": "Выручка",
    "financing": "Кредиты и собственник",
    "tax": "Налоги",
    "payroll": "Зарплата",
    "fixed": "Постоянные расходы",
    "operations": "Закупки и подрядчики",
    "other": "Прочее",
    "unknown": "Без категории",
}


class SourcesMixin:
    # ------------------------------------------------------------ dictionaries and history views

    @staticmethod
    def dictionaries() -> dict:
        return {
            "categories": [
                {
                    "code": c.value,
                    "label": CATEGORY_LABELS[c.value],
                    "group": category_group(c.value),
                    "is_revenue": c.value in REVENUE_CATEGORIES,
                    "is_financing": c.value in FINANCING_CATEGORIES,
                    "is_fixed_cost": c.value in FIXED_COST_CATEGORIES,
                }
                for c in Category
            ],
            "sources": [
                {
                    "code": k,
                    "label": v,
                    "short_label": SOURCE_SHORT_LABELS[k],
                    "kind": "assumption" if k in ASSUMPTION_SOURCES else "confirmed",
                }
                for k, v in SOURCE_LABELS.items()
            ],
            "certainty": [{"code": k, "label": v} for k, v in CERTAINTY_LABELS.items()],
            "groups": [{"code": k, "label": v} for k, v in GROUP_LABELS.items()],
        }

    def latest_date(self, account_id) -> date:
        daily = self.store.daily(account_id)
        if not daily:
            raise ValueError("По счёту нет операций")
        return date.fromisoformat(daily[-1]["date"])

    def categories_summary(
        self, account_id, date_from: date | None, date_to: date | None, filters: dict | None = None
    ):
        date_to = min(date_to or date.max, self.latest_date(account_id))
        date_from = date_from or date_to - timedelta(days=89)
        if date_from > date_to:
            raise ValueError("Начало периода позже конца")
        totals = defaultdict(lambda: {"amount_minor": 0, "operations": 0})
        for tx in self.store.transactions(account_id, date_to):
            if tx["operation_date"] < str(date_from):
                continue
            # The same filters as the operations list, so the chart and the list agree.
            if filters and not self._operation_match(tx, filters):
                continue
            row = totals[(tx["direction"], tx["category"])]
            row["amount_minor"] += tx["amount_minor"]
            row["operations"] += 1
        flows = {
            d: sum(v["amount_minor"] for (dd, _), v in totals.items() if dd == d)
            for d in ("in", "out")
        }
        rows = [
            {
                "direction": d,
                "category": c,
                "label": CATEGORY_LABELS.get(c, c),
                "group": category_group(c),
                **v,
                "share": round(v["amount_minor"] / flows[d], 4) if flows[d] else 0.0,
            }
            for (d, c), v in sorted(
                totals.items(), key=lambda kv: (kv[0][0], -kv[1]["amount_minor"])
            )
        ]
        groups = defaultdict(lambda: {"amount_minor": 0, "operations": 0})
        for r in rows:
            g = groups[(r["direction"], r["group"])]
            g["amount_minor"] += r["amount_minor"]
            g["operations"] += r["operations"]
        return {
            "date_from": str(date_from),
            "date_to": str(date_to),
            "inflow_minor": flows["in"],
            "outflow_minor": flows["out"],
            "rows": rows,
            "groups": [
                {
                    "direction": d,
                    "group": g,
                    "label": GROUP_LABELS[g],
                    **v,
                    "share": round(v["amount_minor"] / flows[d], 4) if flows[d] else 0.0,
                }
                for (d, g), v in sorted(
                    groups.items(), key=lambda kv: (kv[0][0], -kv[1]["amount_minor"])
                )
            ],
        }

    def recurring(self, account_id, as_of: date):
        """Repeating receipts and payments found in the history up to as_of (weekly, fortnightly, monthly)."""
        txs = self.store.transactions(account_id, as_of)
        if not txs:
            raise ValueError("Недостаточно истории на выбранную дату")
        components = detect_components(txs, as_of)
        upcoming = predicted_events(components, account_id, as_of)
        self._tag_counterparties(upcoming, txs)
        by_component = defaultdict(list)
        for e in upcoming:
            by_component[e.component_id].append(e)
        rows = []
        for key, c in components.items():
            events = by_component.get(key, [])
            rows.append(
                {
                    "component_id": key,
                    "category": c["category"],
                    "label": CATEGORY_LABELS.get(c["category"], c["category"]),
                    "direction": c["direction"],
                    "rhythm": c["rhythm"],
                    "rhythm_label": {
                        "weekly": "каждую неделю",
                        "fortnight": "раз в две недели",
                        "monthly": f"ежемесячно, около {c['month_day']}-го",
                    }[c["rhythm"]],
                    "amount_minor": c["amount_minor"],
                    "last_date": str(c["last"]),
                    "counterparty_inn": events[0].counterparty_inn if events else None,
                    "counterparty_name": events[0].counterparty_name if events else None,
                    "next_dates": [str(e.expected_date) for e in events],
                }
            )
        rows.sort(key=lambda r: (r["direction"], -r["amount_minor"]))
        return {
            "as_of": str(as_of),
            "method": "regular-v1: повтор по контрагенту и категории за 180 дней",
            "components": rows,
        }

    # ------------------------------------------------------------ tax calendar

    def _legal_form(self, account_id):
        return structural.legal_form_from_name(self.store.account(account_id)["name"])

    def tax_calendar(self, account_id, as_of: date, horizon: int = 90, run_id=None):
        txs = self.store.transactions(account_id, as_of)
        if not txs:
            raise ValueError("Недостаточно истории на выбранную дату")
        events, cal = tax_events(txs, as_of, self._legal_form(account_id), horizon=horizon)
        base = self.get_forecast(run_id) if run_id else None
        if base and base.account_id != account_id:
            raise ValueError("Прогноз относится к другому счёту")
        regime = cal.regime
        rules = [TAX_RULES["common"]] + (
            [TAX_RULES[regime.regime]] if regime.regime in TAX_RULES else []
        )
        rows = []
        for e in events:
            planned = PlannedEvent(
                id=integrations.event_id("tax", account_id, e.date),
                account_id=account_id,
                component_id=e.component_id[:160],
                category="tax",
                amount_minor=e.amount_minor,
                direction="out",
                due_date=e.date,
                expected_date=e.date,
                known_at=as_of,
                source="tax",
                status="confirmed",
            )
            target = self._matching_assumption(planned, base, set()) if base else None
            rows.append(
                {
                    "date": str(e.date),
                    "amount_minor": e.amount_minor,
                    "label": "Единый налоговый платёж"
                    + (" с авансом по налогу режима" if cal.is_advance_month(e.date.month) else ""),
                    "method": "налоговый календарь режима"
                    if e.mechanism == "tax_calendar"
                    else "медиана прошлых ЕНП",
                    "window": [str(d) for d in e.window] if e.window else None,
                    "in_horizon_30d": e.date <= as_of + timedelta(days=30),
                    "replaces_event_id": target.id if target else None,
                }
            )
        return {
            "as_of": str(as_of),
            "regime": {**regime.to_dict(), "label": REGIME_LABELS[regime.regime]},
            "rules": rules,
            "events": rows,
            "note": "Режим и суммы выведены по истории ЕНП и выручки до даты расчёта; подтвердите их, чтобы добавить в прогноз как налоговые платежи.",
        }

    def apply_tax_calendar(self, account_id, base_run_id, principal):
        base = self.get_forecast(base_run_id)
        if base.account_id != account_id:
            raise ValueError("Прогноз относится к другому счёту")
        self.authorize(principal, account_id)
        calendar_view = self.tax_calendar(account_id, base.as_of, horizon=30)
        events = [
            PlannedEvent(
                id=integrations.event_id("tax", account_id, row["date"]),
                account_id=account_id,
                component_id="tax-calendar",
                category="tax",
                amount_minor=row["amount_minor"],
                direction="out",
                due_date=date.fromisoformat(row["date"]),
                expected_date=date.fromisoformat(row["date"]),
                known_at=base.as_of,
                source="tax",
                status="confirmed",
                label=f"{row['label']} · {calendar_view['regime']['label']}",
            )
            for row in calendar_view["events"]
            if row["in_horizon_30d"]
        ]
        if not events:
            return {
                "created": 0,
                "revised": 0,
                "unchanged": 0,
                "replaced_assumptions": [],
                "events": [],
            }
        result = self.register_events(
            account_id, events, principal, base_run_id, provider="tax_calendar"
        )
        additional = len(result["events"]) - len(result["replaced_assumptions"])
        if additional and base.model != "structural":
            result["warning"] = (
                f"{additional} налоговых платежей добавлены сверх прогноза модели: модель уже частично "
                "учитывает налоги в среднем потоке, возможен двойной учёт. В структурной модели налоговый "
                "календарь встроен без этого риска."
            )
        return result

    # ------------------------------------------------------------ one register for every source

    @staticmethod
    def _same_flow(a: PlannedEvent, b: PlannedEvent) -> bool:
        if a.counterparty_inn and b.counterparty_inn:
            return a.counterparty_inn == b.counterparty_inn
        return a.category == b.category and a.category in SAME_FLOW_CATEGORIES

    def _matching_assumption(self, event: PlannedEvent, base, taken: set):
        """The history-based expectation this confirmed event describes (±7 days, same flow)."""
        candidates = [
            e
            for e in base.events
            if e.source in ASSUMPTION_SOURCES
            and e.id not in taken
            and e.direction == event.direction
            and e.expected_date > base.as_of
            and abs((e.expected_date - event.expected_date).days) <= 7
            and self._same_flow(e, event)
        ]
        return min(
            candidates,
            key=lambda e: abs((e.expected_date - event.expected_date).days),
            default=None,
        )

    def register_events(
        self, account_id, events, principal, base_run_id=None, provider=None, extra=None
    ):
        """Add events from one source. A repeated sync revises changed items and skips equal ones;
        with a base forecast an item replaces the matching history-based assumption."""
        self.authorize(principal, account_id)
        base = None
        if base_run_id:
            base = self.get_forecast(base_run_id)
            self.authorize(principal, base.account_id)
            if base.account_id != account_id:
                raise ValueError("Базовый прогноз относится к другому счёту")
        created, revised, unchanged, replaced = [], [], 0, []
        with self.lock, self.store.connect() as db:
            taken = set()
            for row in db.execute(
                "SELECT payload FROM event_revisions WHERE account_id=?", (account_id,)
            ):
                other = PlannedEvent.model_validate_json(row[0])
                if other.replacement_id:
                    taken.add(other.replacement_id)
            for event in events:
                if event.account_id != account_id:
                    raise ValueError("Событие относится к другому счёту")
                old = db.execute(
                    "SELECT revision,known_at,payload FROM event_revisions WHERE event_id=? ORDER BY revision DESC LIMIT 1",
                    (event.id,),
                ).fetchone()
                if old:
                    previous = PlannedEvent.model_validate_json(old["payload"])
                    # The replacement decided at first registration stays with the event.
                    event.decision, event.replacement_id = (
                        previous.decision,
                        previous.replacement_id,
                    )
                    event.component_id = previous.component_id
                    event.known_at = max(event.known_at, previous.known_at)
                    if event.model_dump(exclude={"known_at"}) == previous.model_dump(
                        exclude={"known_at"}
                    ):
                        unchanged += 1
                        continue
                    db.execute(
                        "INSERT INTO event_revisions VALUES(?,?,?,?,?)",
                        (
                            event.id,
                            account_id,
                            old["revision"] + 1,
                            str(event.known_at),
                            event.model_dump_json(),
                        ),
                    )
                    revised.append(event)
                    continue
                target = self._matching_assumption(event, base, taken) if base else None
                if target and event.known_at >= base.as_of:
                    event.decision, event.replacement_id = "replace", target.id
                    event.component_id = target.component_id
                    taken.add(target.id)
                    replaced.append(
                        {"event_id": event.id, "replaced_id": target.id, "label": target.label}
                    )
                self.add_event(
                    EventCreate(
                        event=event,
                        base_run_id=base_run_id if event.decision == "replace" else None,
                    ),
                    principal,
                    connection=db,
                )
                created.append(event)
            if revised:
                db.execute(
                    "UPDATE accounts SET event_version=event_version+1 WHERE id=?", (account_id,)
                )
            summary = {
                "created": len(created),
                "revised": len(revised),
                "unchanged": unchanged,
                **(extra or {}),
            }
            if provider:
                db.execute(
                    "INSERT INTO integration_syncs VALUES(?,?,?,?)",
                    (account_id, provider, _now(), json.dumps(summary, ensure_ascii=False)),
                )
        return {
            **summary,
            "replaced_assumptions": replaced,
            "events": [e.model_dump(mode="json") for e in created + revised],
        }

    def _known_at(self, account_id, known_at, base_run_id):
        if known_at:
            return known_at
        if base_run_id:
            return self.get_forecast(base_run_id).as_of
        return self.latest_date(account_id)

    def add_contract(self, account_id, spec, principal):
        self.authorize(principal, account_id)
        known_at = self._known_at(account_id, spec.known_at, spec.base_run_id)
        events, skipped = integrations.contract_events(spec, account_id, known_at)
        if not events:
            raise ValueError(
                "Все платежи по договору раньше даты знания: проверьте даты или отметьте оплату"
            )
        result = self.register_events(
            account_id, events, principal, spec.base_run_id, provider="contract"
        )
        return {**result, "skipped_past_due": skipped}

    def add_invoices(self, account_id, specs, principal, provider="invoice"):
        self.authorize(principal, account_id)
        events, delays = [], []
        base_ids = {s.base_run_id for s in specs}
        if len(base_ids) > 1:
            raise ValueError("Счета одного пакета должны ссылаться на один базовый прогноз")
        for spec in specs:
            known_at = self._known_at(account_id, spec.known_at, spec.base_run_id)
            history = self.store.transactions(account_id, known_at)
            event, delay = integrations.invoice_event(spec, account_id, known_at, history)
            events.append(event)
            delays.append({"number": spec.number, "event_id": event.id, **delay})
        result = self.register_events(
            account_id, events, principal, base_ids.pop(), provider=provider
        )
        return {**result, "payment_dates": delays}

    def import_invoices(self, account_id, payload, principal, base_run_id=None, known_at=None):
        items = integrations.parse_invoice_csv(payload)
        specs = [InvoiceSpec(**item, base_run_id=base_run_id, known_at=known_at) for item in items]
        return self.add_invoices(account_id, specs, principal, provider="invoice_import")

    def add_feed(self, account_id, feed, principal, provider):
        self.authorize(principal, account_id)
        known_at = self._known_at(account_id, feed.known_at, feed.base_run_id)
        build = {
            "bank": integrations.bank_events,
            "marketplace": integrations.marketplace_events,
            "ofd": integrations.ofd_events,
        }[provider]
        events = build(feed, account_id, known_at)
        if not events:
            raise ValueError("Нет будущих событий после даты знания")
        return self.register_events(
            account_id, events, principal, feed.base_run_id, provider=provider
        )

    def import_1c(self, account_id, payload, principal, opening_minor=None):
        csv_payload, info = integrations.parse_client_bank_exchange(
            payload, account_id, opening_minor
        )
        report = self.import_transactions(csv_payload, principal)
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO integration_syncs VALUES(?,?,?,?)",
                (
                    account_id,
                    "1c",
                    _now(),
                    json.dumps({"inserted": report["inserted"], "documents": info["documents"]}),
                ),
            )
        return {**report, "source": info}

    # ------------------------------------------------------------ open registries

    def counterparty_check(self, inn, principal, max_age_days=7):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT checked_at,payload FROM counterparty_checks WHERE inn=?", (inn,)
            ).fetchone()
        if row and datetime.fromisoformat(row[0]) > datetime.now(UTC) - timedelta(
            days=max_age_days
        ):
            return {**json.loads(row[1]), "cached": True}
        # Demo visitors never trigger a paid external lookup.
        registry = integrations.CounterpartyRegistry(
            api_key="" if principal["id"] == "demo" else None
        )
        result = registry.check(inn)
        if result["source"] != "offline":
            with self.store.connect() as db:
                db.execute(
                    "INSERT OR REPLACE INTO counterparty_checks VALUES(?,?,?)",
                    (inn, result["checked_at"], json.dumps(result, ensure_ascii=False)),
                )
        return {**result, "cached": False}

    def counterparty_risks(self, account_id, as_of, principal, limit=10):
        """Largest clients and suppliers of the last 90 days checked against the registry."""
        since = str(as_of - timedelta(days=90))
        totals = {}
        for tx in self.store.transactions(account_id, as_of):
            inn = tx["counterparty_inn"]
            if tx["operation_date"] <= since or not inn:
                continue
            role = (
                "client"
                if is_revenue(tx["category"], tx["direction"])
                else "supplier"
                if tx["direction"] == "out"
                and tx["category"]
                in {"supplier", "contractor", "operating_expense", "logistics", "rent"}
                else None
            )
            if role is None:
                continue
            row = totals.setdefault(
                (role, inn),
                {
                    "role": role,
                    "counterparty_inn": inn,
                    "counterparty_name": tx["counterparty_name"],
                    "amount_minor": 0,
                },
            )
            row["amount_minor"] += tx["amount_minor"]
        rows = []
        for role in ("client", "supplier"):
            top = sorted(
                (r for r in totals.values() if r["role"] == role), key=lambda r: -r["amount_minor"]
            )[:limit]
            for r in top:
                check = self.counterparty_check(r["counterparty_inn"], principal)
                item = {**r, "check": check}
                if check["risk"] in ("critical", "warning") and role == "client":
                    item["suggested_scenario"] = {
                        "kind": "client_delay",
                        "counterparty": r["counterparty_inn"],
                        "delay_days": 28,
                    }
                rows.append(item)
        flagged = [r for r in rows if r["check"]["risk"] in ("critical", "warning")]
        return {
            "as_of": str(as_of),
            "checked": len(rows),
            "flagged": len(flagged),
            "counterparties": rows,
        }

    # ------------------------------------------------------------ plan-fact, refresh, alerts

    def plan_fact_view(self, run_id, granularity="day"):
        result = self.plan_fact(run_id)
        base = self.get_forecast(run_id)
        latest = self.latest_date(base.account_id)
        # Deviation worth a highlight: 10% of the opening balance or 100 thousand rubles.
        threshold = max(abs(base.opening_balance_minor) // 10, 10_000_000)
        compared = [r for r in result["rows"] if r["actual_minor"] is not None]
        for r in result["rows"]:
            r["deviation"] = r["delta_minor"] is not None and abs(r["delta_minor"]) >= threshold
        overdue = []
        for e in self.events(base.account_id, latest):
            if (
                e.status in ("paid", "cancelled")
                or not e.remaining_minor
                or e.expected_date > latest
            ):
                continue
            if e.expected_date <= base.as_of:
                continue
            overdue.append(
                {
                    "event_id": e.id,
                    "label": e.label,
                    "source": e.source,
                    "direction": e.direction,
                    "expected_date": str(e.expected_date),
                    "days_overdue": (latest - e.expected_date).days,
                    "remaining_minor": e.remaining_minor,
                    "counterparty_name": e.counterparty_name,
                    "warning": f"Ожидаемый платёж {fmt_money(e.remaining_minor)} от {fmt_day(e.expected_date)} не поступил"
                    if e.direction == "in"
                    else f"Запланированный платёж {fmt_money(e.remaining_minor)} от {fmt_day(e.expected_date)} не проведён",
                }
            )
        result["overdue"] = overdue
        result["summary"] = {
            "days_compared": len(compared),
            "deviation_threshold_minor": threshold,
            "deviation_days": sum(r["deviation"] for r in result["rows"]),
            "mean_abs_delta_minor": int(
                sum(abs(r["delta_minor"]) for r in compared) / len(compared)
            )
            if compared
            else None,
            "max_abs_delta_minor": max((abs(r["delta_minor"]) for r in compared), default=None),
            "latest_data_date": str(latest),
        }
        result["granularity"] = granularity
        if granularity == "week":
            weeks = {}
            for r in result["rows"]:
                d = date.fromisoformat(r["date"])
                start = d - timedelta(days=d.weekday())
                w = weeks.setdefault(
                    start,
                    {
                        "week_start": str(start),
                        "week_end": str(start + timedelta(days=6)),
                        "planned_in_minor": 0,
                        "planned_out_minor": 0,
                        "actual_in_minor": 0,
                        "actual_out_minor": 0,
                        "days": 0,
                        "days_with_fact": 0,
                    },
                )
                w["planned_in_minor"] += r["planned_in_minor"]
                w["planned_out_minor"] += r["planned_out_minor"]
                w["days"] += 1
                w["planned_minor"] = r["planned_minor"]
                if r["actual_minor"] is not None:
                    w["actual_in_minor"] += r["actual_in_minor"]
                    w["actual_out_minor"] += r["actual_out_minor"]
                    w["days_with_fact"] += 1
                    w["actual_minor"] = r["actual_minor"]
            for w in weeks.values():
                complete = w["days_with_fact"] == w["days"]
                w.setdefault("actual_minor", None)
                if not complete:
                    w["actual_minor"] = w["actual_minor"] if w["days_with_fact"] else None
                w["complete"] = complete
                w["delta_minor"] = (
                    w["actual_minor"] - w["planned_minor"]
                    if w["actual_minor"] is not None
                    else None
                )
                w["deviation"] = w["delta_minor"] is not None and abs(w["delta_minor"]) >= threshold
            result["weeks"] = list(weeks.values())
        return result

    def refresh(self, run_id, principal):
        """Re-check the forecast after new data. Only events changed: re-apply them to the stored
        model trajectory (no model run). New statement data: forecast for the latest date (no
        training). Either way the risk is checked again and the change is reported."""
        started = time.perf_counter()
        base = self.get_forecast(run_id)
        self.authorize(principal, base.account_id)
        account = self.store.account(base.account_id)
        latest = self.latest_date(base.account_id)
        if account["data_version"] != base.data_version:
            mode = "new_data"
        elif account["event_version"] != base.event_version:
            mode = "events_only"
        else:
            mode = "unchanged"
        if mode == "unchanged":
            new = base
        elif mode == "events_only":
            new = self._reapply_events(base, account)
            if new is None:
                mode = "events_full"
                new = self.forecast(
                    ForecastRequest(
                        account_id=base.account_id,
                        as_of=base.as_of,
                        model=base.model,
                        calendar=base.calendar,
                        reserve_minor=base.reserve_minor,
                        credit_limit_minor=base.credit_limit_minor,
                    )
                )
        else:
            new = self.forecast(
                ForecastRequest(
                    account_id=base.account_id,
                    as_of=latest,
                    model=base.model,
                    calendar=base.calendar,
                    reserve_minor=base.reserve_minor,
                    credit_limit_minor=base.credit_limit_minor,
                )
            )
        old_gap, new_gap = base.risk.first_gap_date, new.risk.first_gap_date
        changes = {
            "gap_appeared": old_gap is None and new_gap is not None,
            "gap_resolved": old_gap is not None and new_gap is None,
            "gap_shift_days": (new_gap - old_gap).days if old_gap and new_gap else None,
            "need_delta_minor": new.risk.liquidity_need_minor - base.risk.liquidity_need_minor,
            "minimum_delta_minor": new.risk.minimum_balance_minor - base.risk.minimum_balance_minor,
        }
        return {
            "mode": mode,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "previous": {
                "run_id": base.run_id,
                "as_of": str(base.as_of),
                "first_gap_date": str(old_gap) if old_gap else None,
                "liquidity_need_minor": base.risk.liquidity_need_minor,
            },
            "changes": changes,
            "forecast": new.model_dump(mode="json"),
        }

    # ------------------------------------------------------------ overdue events, downside view

    def _overdue_policy(self, registered, as_of):
        """A confirmed obligation that was not paid stays due: it moves to the next working day.
        A late receipt is not counted until it gets a new date or a payment."""
        tomorrow = next_working_day(as_of + timedelta(days=1))
        adjusted, overdue = [], []
        for e in registered:
            late = (
                e.expected_date <= as_of
                and e.remaining_minor
                and e.status not in ("cancelled", "paid", "review")
            )
            if not late:
                adjusted.append(e)
                continue
            item = {
                "event_id": e.id,
                "label": e.label,
                "source": e.source,
                "direction": e.direction,
                "expected_date": str(e.expected_date),
                "remaining_minor": e.remaining_minor,
                "days_overdue": (as_of - e.expected_date).days,
            }
            if e.direction == "out":
                adjusted.append(e.model_copy(update={"expected_date": tomorrow}))
                item |= {
                    "treatment": "rolled_forward",
                    "new_date": str(tomorrow),
                    "note": "Неоплаченное обязательство остаётся в прогнозе на ближайший рабочий день",
                }
            else:
                adjusted.append(e)
                item |= {
                    "treatment": "excluded",
                    "new_date": None,
                    "note": "Поступление просрочено и не учитывается, пока нет новой даты или оплаты",
                }
            overdue.append(item)
        return adjusted, overdue

    def _registry_risk(self, inn):
        check = self._registry_check(inn)
        return check["risk"] if check else None

    def _stress(self, as_of, opening, values, events, reserve=0, credit=0):
        """The forecast without receipts that may not come: promises and risky counterparties."""
        end = as_of + timedelta(days=len(values))
        excluded = []
        for e in events:
            if (
                e.direction != "in"
                or not as_of < e.expected_date <= end
                or e.known_at > as_of
                or e.status in ("cancelled", "paid", "review")
                or not e.remaining_minor
            ):
                continue
            if e.certainty == "promised":
                reason = "обещание без документа"
            elif self._registry_risk(e.counterparty_inn) in ("critical", "warning"):
                reason = "контрагент с риском в реестре"
            else:
                continue
            excluded.append((e, reason))
        if not excluded:
            return None
        ids = {e.id for e, _ in excluded}
        points = assemble(as_of, opening, values, [e for e in events if e.id not in ids])
        risk = deterministic_risk(opening, points, reserve, credit)
        return {
            "description": "Если обещанные поступления и деньги от рискованных контрагентов не придут",
            "excluded": [
                {
                    "event_id": e.id,
                    "label": e.label,
                    "expected_date": str(e.expected_date),
                    "amount_minor": e.remaining_minor,
                    "reason": reason,
                }
                for e, reason in excluded
            ],
            "excluded_minor": sum(e.remaining_minor for e, _ in excluded),
            "first_gap_date": str(risk.first_gap_date) if risk.first_gap_date else None,
            "liquidity_need_minor": risk.liquidity_need_minor,
            "minimum_balance_minor": risk.minimum_balance_minor,
        }

    def _reapply_events(self, base, account):
        """Same as-of and statement: keep the model's daily flows, rebuild only the event layer.
        Returns None when the stored flows cannot be restored (a stream was fully displaced)."""
        registered, overdue = self._overdue_policy(
            self.events(base.account_id, base.as_of), base.as_of
        )
        replaced = {e.replacement_id for e in registered if e.decision == "replace"}
        system = [e for e in base.events if e.source in ASSUMPTION_SOURCES and e.id not in replaced]
        residuals = [
            [
                p.residual_revenue_minor,
                p.residual_in_minor - p.residual_revenue_minor,
                p.residual_out_minor,
            ]
            for p in base.points
        ]
        if base.netting:
            factors = base.netting["factors"]
            if min(factors) <= 0:
                return None
            # Undo the previous replacement, then apply it again for the current register.
            residuals = [[row[s] / factors[s] for s in range(3)] for row in residuals]
        txs = self.store.transactions(base.account_id, base.as_of)
        residuals, netting = net_known_events(
            residuals, registered, base.as_of, flow_shares(txs, base.as_of)
        )
        events = system + registered
        opening = base.opening_balance_minor
        points = assemble(base.as_of, opening, residuals, events)
        risk = deterministic_risk(opening, points, base.reserve_minor, base.credit_limit_minor)
        if base.model != "structural" and base.risk.calibration_status != "unavailable":
            _, _, scale = prepare(txs, base.as_of, base.calendar)
            risk = add_uncertainty(
                risk,
                points,
                base.as_of,
                f"{base.model}|{base.model_version}|calendar={base.calendar}",
                scale,
                self.artifacts,
                bool(registered),
                events=events,
            )
        else:
            risk.limitation = base.risk.limitation
        risk.stress = self._stress(
            base.as_of, opening, residuals, events, base.reserve_minor, base.credit_limit_minor
        )
        stale = (
            "Событие ",
            "Просрочено событие",
            "Просроченное обязательство",
            "Подтверждённые события заменили",
        )
        flags = [f for f in base.quality_flags if not f.startswith(stale)]
        for e in registered:
            if e.decision == "additional":
                flags.append(f"Событие {e.id}: пользователь выбрал дополнительное движение")
        for item in overdue:
            flags.append(f"Просрочено событие {item['event_id']}: {item['note'].lower()}")
        if netting:
            flags.append(
                f"Подтверждённые события заменили {netting['displaced_minor']} коп. среднего потока "
                "модели, чтобы одни и те же деньги не учитывались дважды"
            )
        result = base.model_copy(
            update={
                "run_id": str(uuid.uuid4()),
                "event_version": account["event_version"],
                "points": points,
                "events": events,
                "risk": risk,
                "netting": netting,
                "overdue_events": overdue,
                "quality_flags": flags
                + ["Обновлено без запуска модели: изменились только события реестра"],
                "created_at": _now(),
            }
        )
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO forecasts VALUES(?,?,?)",
                (result.run_id, result.account_id, result.model_dump_json()),
            )
        return result

    # ------------------------------------------------------------ plan-fact: link events to the statement

    @staticmethod
    def _match(e: PlannedEvent, tx: dict, free: int):
        """(score, reasons) of an operation as the payment of an event."""
        score, reasons = 0, []
        name = (e.counterparty_name or "").strip(' «»"').casefold()
        if e.counterparty_inn and tx["counterparty_inn"] == e.counterparty_inn:
            score, reasons = score + 3, reasons + ["ИНН"]
        elif name and name == tx["counterparty_name"].strip(' «»"').casefold():
            score, reasons = score + 2, reasons + ["название контрагента"]
        number = re.search(r"№\s*([\w./-]+)", e.document_ref or "")
        if number and re.search(
            rf"№\s*{re.escape(number.group(1))}(?![\w/])", tx["payment_purpose"] or ""
        ):
            score, reasons = score + 3, reasons + ["номер документа"]
        identified = bool(reasons)
        if free == e.remaining_minor:
            score, reasons = score + 3, reasons + ["сумма"]
        elif abs(free - e.remaining_minor) <= e.remaining_minor // 100:
            score, reasons = score + 2, reasons + ["сумма ±1%"]
        elif free < e.remaining_minor:
            score, reasons = score + 1, reasons + ["частичная оплата"]
        days = abs((date.fromisoformat(tx["operation_date"]) - e.expected_date).days)
        if days <= 3:
            score, reasons = score + 1, reasons + ["дата"]
        same_kind = tx["category"] == e.category or (
            is_revenue(tx["category"], tx["direction"]) and is_revenue(e.category, e.direction)
        )
        if same_kind:
            score, reasons = score + 1, reasons + ["категория"]
        return score, reasons, identified

    def reconcile(self, account_id, principal, dry_run=False):
        """Mark registered events as paid by operations of the statement.

        An operation of the same direction within [-10, +45] days of the expected date is scored by
        INN or name, document number in the purpose, amount, date and category. Only a clear best
        match (score >= 6, identified by the party or the document, 2 points above the next) is
        linked; close calls go to review. Linking records the payment with its date of knowledge.
        """
        self.authorize(principal, account_id)
        events = [
            e
            for e in self.events(account_id, date.max)
            if e.source not in ASSUMPTION_SOURCES
            and e.status not in ("cancelled", "paid")
            and e.remaining_minor > 0
        ]
        linked, review = [], []
        if not events:
            return {
                "checked_events": 0,
                "linked": linked,
                "needs_review": review,
                "dry_run": dry_run,
            }
        txs = self.store.transactions(account_id)
        with self.store.connect() as db:
            used = {
                r[0]: r[1]
                for r in db.execute(
                    "SELECT operation_id, SUM(amount_minor) FROM payments WHERE account_id=? GROUP BY operation_id",
                    (account_id,),
                )
            }
        for e in sorted(events, key=lambda e: (e.expected_date, e.id)):
            low, high = e.expected_date - timedelta(days=10), e.expected_date + timedelta(days=45)
            scored = []
            for tx in txs:
                if tx["direction"] != e.direction or tx["operation_date"] < str(
                    max(low, e.known_at - timedelta(days=10))
                ):
                    continue
                if tx["operation_date"] > str(high):
                    continue
                free = tx["amount_minor"] - used.get(tx["operation_id"], 0)
                if free <= 0:
                    continue
                score, reasons, identified = self._match(e, tx, free)
                if score >= 4 and identified:
                    scored.append((score, tx, free, reasons))
            if not scored:
                continue
            scored.sort(key=lambda s: (-s[0], s[1]["operation_date"]))
            best = scored[0]
            runner_up = scored[1][0] if len(scored) > 1 else 0
            options = [
                {
                    "operation_id": tx["operation_id"],
                    "operation_date": tx["operation_date"],
                    "amount_minor": free,
                    "score": score,
                    "reasons": reasons,
                }
                for score, tx, free, reasons in scored[:3]
            ]
            if best[0] >= 6 and best[0] - runner_up >= 2:
                score, tx, free, reasons = best
                amount = min(free, e.remaining_minor)
                known_at = max(date.fromisoformat(tx["operation_date"]), e.known_at)
                if not dry_run:
                    self.link_payment(
                        e.id,
                        PaymentLink(
                            operation_id=tx["operation_id"], amount_minor=amount, known_at=known_at
                        ),
                        principal,
                    )
                used[tx["operation_id"]] = used.get(tx["operation_id"], 0) + amount
                linked.append(
                    {
                        "event_id": e.id,
                        "label": e.label,
                        "operation_id": tx["operation_id"],
                        "operation_date": tx["operation_date"],
                        "amount_minor": amount,
                        "fully_paid": amount == e.remaining_minor,
                        "score": score,
                        "reasons": reasons,
                    }
                )
            else:
                review.append({"event_id": e.id, "label": e.label, "candidates": options})
        return {
            "checked_events": len(events),
            "linked": linked,
            "needs_review": review,
            "dry_run": dry_run,
        }

    def _registry_check(self, inn):
        """Registry answer from the cache or the offline demo file: never a network call."""
        if not inn:
            return None
        if integrations.is_synthetic_inn(inn):
            if not hasattr(self, "_offline_registry"):
                self._offline_registry = integrations.CounterpartyRegistry(api_key="")
            return self._offline_registry.check(inn)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM counterparty_checks WHERE inn=?", (inn,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def alerts(self, account_id, principal, run_id=None):
        """Structured warnings for the latest (or given) forecast of the account."""
        self.authorize(principal, account_id)
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM forecasts WHERE account_id=? ORDER BY rowid DESC LIMIT 2",
                (account_id,),
            ).fetchall()
        if run_id:
            current = self.get_forecast(run_id)
            if current.account_id != account_id:
                raise ValueError("Прогноз относится к другому счёту")
            previous = None
        elif rows:
            current = ForecastResult.model_validate_json(rows[0][0])
            previous = ForecastResult.model_validate_json(rows[1][0]) if len(rows) > 1 else None
        else:
            return {"run_id": None, "alerts": [], "note": "Прогноз ещё не рассчитан"}
        risk, alerts = current.risk, []
        if risk.current_gap_minor:
            alerts.append(
                {
                    "kind": "current_deficit",
                    "severity": "critical",
                    "date": str(current.as_of),
                    "amount_minor": risk.current_gap_minor,
                    "title": f"Уже не хватает {fmt_money(risk.current_gap_minor, up=True)}",
                    "text": "Остаток ниже порога на дату расчёта.",
                }
            )
        if risk.new_risk and risk.first_gap_date:
            days = (risk.first_gap_date - current.as_of).days
            probability = (
                f", вероятность {round(risk.probability_30d * 100)}%"
                if risk.probability_30d is not None
                else ""
            )
            alerts.append(
                {
                    "kind": "new_gap",
                    "severity": "critical" if days <= 7 else "warning",
                    "date": str(risk.first_gap_date),
                    "amount_minor": risk.liquidity_need_minor,
                    "title": f"{fmt_day(risk.first_gap_date)} не хватит {fmt_money(risk.liquidity_need_minor, up=True)}",
                    "text": f"Кассовый разрыв через {days} дн.{probability}.",
                    "action": {"type": "explanation", "run_id": current.run_id},
                }
            )
        elif risk.alarm and not risk.current_gap_minor:
            # X11: the median stays above zero, the calibrated probability is past the threshold.
            alerts.append(
                {
                    "kind": "gap_risk",
                    "severity": "warning",
                    "date": None,
                    "amount_minor": 0,
                    "title": f"Риск разрыва {round(risk.probability_30d * 100)}%",
                    "text": "Прогноз не уходит в минус, но запас тонкий: в похожих ситуациях "
                    f"денег не хватало. Минимум {fmt_money(risk.minimum_balance_minor)}.",
                    "action": {"type": "explanation", "run_id": current.run_id},
                }
            )
        if (
            previous
            and previous.risk.first_gap_date
            and risk.first_gap_date
            and previous.risk.first_gap_date != risk.first_gap_date
        ):
            shift = (risk.first_gap_date - previous.risk.first_gap_date).days
            alerts.append(
                {
                    "kind": "gap_moved",
                    "severity": "warning" if shift < 0 else "info",
                    "date": str(risk.first_gap_date),
                    "amount_minor": risk.liquidity_need_minor,
                    "title": f"Разрыв сдвинулся на {abs(shift)} дн. {'раньше' if shift < 0 else 'позже'}",
                    "text": f"Было {fmt_day(previous.risk.first_gap_date)}, стало {fmt_day(risk.first_gap_date)}.",
                }
            )
        if previous and previous.risk.first_gap_date and not risk.first_gap_date:
            alerts.append(
                {
                    "kind": "gap_resolved",
                    "severity": "info",
                    "date": None,
                    "amount_minor": 0,
                    "title": "Разрыв больше не прогнозируется",
                    "text": f"Раньше ожидался {fmt_day(previous.risk.first_gap_date)}.",
                }
            )
        # «Today» is the forecast date: in a historical replay later payments are not overdue yet.
        today = current.as_of
        for e in self.events(account_id, today):
            if (
                e.status in ("paid", "cancelled")
                or not e.remaining_minor
                or e.expected_date >= today
            ):
                continue
            alerts.append(
                {
                    "kind": "overdue_payment",
                    "severity": "warning",
                    "date": str(e.expected_date),
                    "amount_minor": e.remaining_minor,
                    "title": (
                        "Не поступил платёж " if e.direction == "in" else "Не проведён платёж "
                    )
                    + fmt_money(e.remaining_minor),
                    "text": f"{e.label} — ожидался {fmt_day(e.expected_date)}. Укажите новую дату или отметьте оплату.",
                    "action": {"type": "reschedule", "event_id": e.id},
                }
            )
        expected = {}
        for e in current.events:
            if (
                e.direction == "in"
                and e.counterparty_inn
                and e.expected_date > current.as_of
                and e.status not in ("cancelled", "paid", "review")
                and e.remaining_minor
            ):
                row = expected.setdefault(
                    e.counterparty_inn,
                    {"amount": 0, "name": e.counterparty_name, "first": e.expected_date},
                )
                row["amount"] += e.remaining_minor
                row["first"] = min(row["first"], e.expected_date)
        for inn, row in expected.items():
            check = self._registry_check(inn)
            if check and check["risk"] in ("critical", "warning"):
                alerts.append(
                    {
                        "kind": "risky_counterparty",
                        "severity": "critical" if check["risk"] == "critical" else "warning",
                        "date": str(row["first"]),
                        "amount_minor": row["amount"],
                        "title": f"{row['name'] or inn}: {check['status_label'].lower()}",
                        "text": f"От контрагента ожидается {fmt_money(row['amount'])} с "
                        f"{fmt_day(row['first'])}. Проверьте прогноз без этой оплаты.",
                        "action": {
                            "type": "scenario",
                            "scenario": {
                                "kind": "client_delay",
                                "counterparty": inn,
                                "delay_days": 28,
                            },
                        },
                    }
                )
        stress = risk.stress
        if (
            stress
            and stress["first_gap_date"]
            and (not risk.first_gap_date or stress["first_gap_date"] < str(risk.first_gap_date))
        ):
            gap = date.fromisoformat(stress["first_gap_date"])
            alerts.append(
                {
                    "kind": "stress_gap",
                    "severity": "warning",
                    "date": stress["first_gap_date"],
                    "amount_minor": stress["liquidity_need_minor"],
                    "title": f"Без ненадёжных поступлений разрыв {fmt_day(gap)}",
                    "text": f"Если не придут {fmt_money(stress['excluded_minor'])} обещанных или "
                    f"рискованных оплат, не хватит {fmt_money(stress['liquidity_need_minor'], up=True)}.",
                    "action": {"type": "stress", "run_id": current.run_id},
                }
            )
        if current.confidence and current.confidence.get("level") in ("none", "very_low", "low"):
            alerts.append(
                {
                    "kind": "data_quality",
                    "severity": "info",
                    "date": None,
                    "amount_minor": 0,
                    "title": current.confidence.get("label", "Низкое доверие"),
                    "text": current.confidence.get("limitation", ""),
                }
            )
        order = {"critical": 0, "warning": 1, "info": 2}
        alerts.sort(key=lambda a: (order[a["severity"]], a["date"] or "9999"))
        return {"run_id": current.run_id, "as_of": str(current.as_of), "alerts": alerts}

    def sources_summary(self, account_id, principal):
        self.authorize(principal, account_id)
        account = self.store.account(account_id)
        quality = self.store.quality(account_id)
        counts = defaultdict(lambda: {"events": 0, "amount_in_minor": 0, "amount_out_minor": 0})
        for e in self.events(account_id, date.max):
            row = counts[e.source]
            row["events"] += 1
            row["amount_in_minor" if e.direction == "in" else "amount_out_minor"] += (
                e.remaining_minor
            )
        with self.store.connect() as db:
            syncs = db.execute(
                "SELECT provider, MAX(synced_at) AS last, COUNT(*) AS n FROM integration_syncs WHERE account_id=? GROUP BY provider",
                (account_id,),
            ).fetchall()
        return {
            "statement": {
                "operations": quality.get("operations"),
                "first_date": quality.get("first_date"),
                "last_date": quality.get("last_date"),
                "data_version": account["data_version"],
                "label": "Банковская выписка",
            },
            "registered_events": [
                {"source": k, "label": SOURCE_LABELS.get(k, k), **v}
                for k, v in sorted(counts.items())
            ],
            "system_sources": [
                {
                    "source": s,
                    "label": SOURCE_LABELS[s],
                    "note": "строится автоматически по истории до даты расчёта",
                }
                for s in sorted(ASSUMPTION_SOURCES)
            ],
            "syncs": [
                {"provider": r["provider"], "last_sync": r["last"], "count": r["n"]} for r in syncs
            ],
        }
