"""Editing the register and the filtered views of the calendar and the operations (Service mixin).

An event is never deleted or overwritten: an edit or a cancellation is a new revision with its own
`known_at`, so a forecast for an earlier date still sees what was known then. A history-based
expectation (regular payment, tax, document) is not stored; the user's correction of it is a
registered event that replaces it, and skipping it is such a replacement cancelled at once.
"""

from collections import Counter, defaultdict
from datetime import date

from cashflow_domain.feeds import AssumptionEdit, EventEdit
from cashflow_domain.schemas import (
    ASSUMPTION_SOURCES,
    CATEGORY_LABELS,
    CERTAINTY_LABELS,
    FIXED_COST_CATEGORIES,
    SOURCE_LABELS,
    SOURCE_SHORT_LABELS,
    EventCreate,
    PlannedEvent,
)
from cashflow_services.sources import category_group

# Outflows the business cannot simply postpone: they define the «обязательные платежи».
OBLIGATORY_CATEGORIES = frozenset({"tax", "salary", "debt_service", *FIXED_COST_CATEGORIES})
HISTORY_SUFFIX = " · повтор по истории"


def _matches(text: str, *fields) -> bool:
    needle = text.casefold().strip()
    return any(needle in (f or "").casefold() for f in fields)


def _facet(counter: Counter, labels: dict) -> list[dict]:
    return [
        {"code": code, "label": labels.get(code, code), "count": count}
        for code, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


class EditingMixin:
    # ------------------------------------------------------------ register revisions

    def _latest_revision(self, db, event_id, principal):
        row = db.execute(
            "SELECT * FROM event_revisions WHERE event_id=? ORDER BY revision DESC LIMIT 1",
            (event_id,),
        ).fetchone()
        if not row:
            raise ValueError("Событие не найдено")
        self.authorize(principal, row["account_id"])
        return row

    def _write_revision(self, db, row, event, known_at):
        if str(known_at) < row["known_at"]:
            raise ValueError("Дата знания должна сохранять порядок версий")
        db.execute(
            "INSERT INTO event_revisions VALUES(?,?,?,?,?)",
            (
                event.id,
                event.account_id,
                row["revision"] + 1,
                str(known_at),
                event.model_dump_json(),
            ),
        )
        db.execute(
            "UPDATE accounts SET event_version=event_version+1 WHERE id=?", (event.account_id,)
        )

    def edit_event(self, event_id, body: EventEdit, principal) -> PlannedEvent:
        with self.lock, self.store.connect() as db:
            row = self._latest_revision(db, event_id, principal)
            current = PlannedEvent.model_validate_json(row["payload"])
            if current.status == "cancelled":
                raise ValueError("Событие отменено; создайте новое")
            changes = body.model_dump(exclude_unset=True, exclude={"known_at"})
            paid = db.execute(
                "SELECT COALESCE(SUM(amount_minor), 0) FROM payments WHERE event_id=?", (event_id,)
            ).fetchone()[0]
            if changes.get("amount_minor") is not None and changes["amount_minor"] < paid:
                raise ValueError("Сумма меньше уже оплаченной части")
            event = PlannedEvent.model_validate(
                current.model_dump() | changes | {"known_at": body.known_at}
            )
            if event.model_copy(update={"known_at": current.known_at}) == current:
                return current
            self._write_revision(db, row, event, body.known_at)
        return event

    def cancel_event(self, event_id, known_at: date, principal) -> PlannedEvent:
        with self.lock, self.store.connect() as db:
            row = self._latest_revision(db, event_id, principal)
            current = PlannedEvent.model_validate_json(row["payload"])
            if current.status == "cancelled":
                return current
            event = current.model_copy(update={"status": "cancelled", "known_at": known_at})
            self._write_revision(db, row, event, known_at)
        return event

    def edit_assumption(self, run_id, assumption_id, body: AssumptionEdit, principal):
        base = self.get_forecast(run_id)
        self.authorize(principal, base.account_id)
        target = next(
            (e for e in base.events if e.id == assumption_id and e.source in ASSUMPTION_SOURCES),
            None,
        )
        if target is None:
            raise ValueError("Ожидание не найдено в этом прогнозе")
        # service.py imports this mixin, so its error class is taken at call time.
        from cashflow_services.service import ConflictError

        done = next(
            (e for e in self.events(target.account_id, date.max) if e.replacement_id == target.id),
            None,
        )
        if done:
            raise ConflictError(f"Ожидание уже исправлено: измените событие {done.id}")
        event = PlannedEvent(
            id=f"override:{target.id}"[:160],
            account_id=target.account_id,
            component_id=target.component_id,
            category=target.category,
            amount_minor=body.amount_minor or target.amount_minor,
            direction=target.direction,
            due_date=target.due_date,
            expected_date=body.expected_date or target.expected_date,
            known_at=body.known_at,
            source="manual",
            status="confirmed",
            decision="replace",
            replacement_id=target.id,
            label=body.label or target.label,
            counterparty_inn=target.counterparty_inn,
            counterparty_name=target.counterparty_name,
            certainty="agreed",
        )
        event = self.add_event(EventCreate(event=event, base_run_id=run_id), principal)
        if body.action == "skip":
            event = self.cancel_event(event.id, body.known_at, principal)
        return event

    # ------------------------------------------------------------ payment calendar

    def calendar_view(self, run_id, principal, filters: dict):
        """Every expected payment of a forecast with what the user can do with it, filtered."""
        base = self.get_forecast(run_id)
        self.authorize(principal, base.account_id)
        horizon_end = base.points[-1].date if base.points else base.as_of
        items = []
        for e in base.events:
            if e.expected_date > horizon_end or e.status == "paid":
                continue
            assumption = e.source in ASSUMPTION_SOURCES
            group = category_group(e.category.value)
            if assumption:
                actions = ["edit", "skip"]
            elif e.status == "cancelled":
                actions = []
            else:
                actions = ["edit", "delete"]
            items.append(
                {
                    "id": e.id,
                    "date": e.expected_date,
                    "due_date": e.due_date,
                    "direction": e.direction,
                    "amount_minor": e.remaining_minor,
                    "label": e.label,
                    # The badge already says where it comes from; the title keeps only the what.
                    "title": e.label.removesuffix(HISTORY_SUFFIX),
                    "component_id": e.component_id,
                    "category": e.category.value,
                    "category_label": CATEGORY_LABELS.get(e.category.value, e.category.value),
                    "group": group,
                    "source": e.source,
                    "source_label": SOURCE_LABELS.get(e.source, e.source),
                    "source_short": SOURCE_SHORT_LABELS.get(e.source, e.source),
                    "kind": "assumption" if assumption else "registered",
                    "status": e.status,
                    "certainty": e.certainty,
                    "certainty_label": CERTAINTY_LABELS.get(e.certainty) if e.certainty else None,
                    "counterparty_name": e.counterparty_name,
                    "counterparty_inn": e.counterparty_inn,
                    "obligatory": e.direction == "out" and e.category in OBLIGATORY_CATEGORIES,
                    "overdue": e.expected_date <= base.as_of,
                    "actions": actions,
                }
            )
        active = [i for i in items if i["status"] != "cancelled"]
        facets = {
            "sources": _facet(Counter(i["source"] for i in active), SOURCE_SHORT_LABELS),
            "categories": _facet(Counter(i["category"] for i in active), CATEGORY_LABELS),
            "directions": _facet(
                Counter(i["direction"] for i in active), {"in": "Поступления", "out": "Списания"}
            ),
            "obligatory": sum(i["obligatory"] for i in active),
            "cancelled": len(items) - len(active),
        }
        selected = [i for i in items if self._calendar_match(i, filters)]
        # Within a day receipts come first, then payments, larger first: the order of a statement.
        selected.sort(key=lambda i: (i["date"], i["direction"] == "out", -i["amount_minor"]))
        live = [i for i in selected if i["status"] != "cancelled"]
        return {
            "run_id": base.run_id,
            "account_id": base.account_id,
            "as_of": base.as_of,
            "date_to": horizon_end,
            "items": selected,
            "totals": {
                "count": len(live),
                "inflow_minor": sum(i["amount_minor"] for i in live if i["direction"] == "in"),
                "outflow_minor": sum(i["amount_minor"] for i in live if i["direction"] == "out"),
            },
            "facets": facets,
        }

    @staticmethod
    def _calendar_match(item, f) -> bool:
        if item["status"] == "cancelled" and not f.get("include_cancelled"):
            return False
        if f.get("date_from") and item["date"] < f["date_from"]:
            return False
        if f.get("date_to") and item["date"] > f["date_to"]:
            return False
        for key, field in (
            ("direction", "direction"),
            ("kind", "kind"),
            ("source", "source"),
            ("category", "category"),
            ("group", "group"),
        ):
            wanted = f.get(key)
            if wanted and item[field] not in wanted:
                return False
        if f.get("obligatory") and not item["obligatory"]:
            return False
        if f.get("amount_min") is not None and item["amount_minor"] < f["amount_min"]:
            return False
        if f.get("amount_max") is not None and item["amount_minor"] > f["amount_max"]:
            return False
        return not f.get("q") or _matches(
            f["q"], item["label"], item["counterparty_name"], item["counterparty_inn"]
        )

    # ------------------------------------------------------------ operations

    def transactions_view(self, account_id, principal, filters: dict, page: int, page_size: int):
        """Statement operations with filters; totals and facets describe the filtered period."""
        self.authorize(principal, account_id)
        date_from, date_to = filters.get("date_from"), filters.get("date_to")
        if date_from and date_to and date_from > date_to:
            raise ValueError("Начало периода позже конца")
        rows = [
            tx
            for tx in self.store.transactions(account_id, date_to)
            if not date_from or tx["operation_date"] >= str(date_from)
        ]
        categories = Counter(tx["category"] for tx in rows)
        counterparties = defaultdict(lambda: {"count": 0, "amount_minor": 0})
        for tx in rows:
            if tx["counterparty_name"] or tx["counterparty_inn"]:
                key = (tx["counterparty_name"], tx["counterparty_inn"])
                counterparties[key]["count"] += 1
                counterparties[key]["amount_minor"] += tx["amount_minor"]
        selected = [tx for tx in rows if self._operation_match(tx, filters)]
        order = filters.get("sort") or "date_desc"
        field = "amount_minor" if order.startswith("amount") else "operation_date"
        selected.sort(
            key=lambda tx: (tx[field], tx["operation_id"]), reverse=order.endswith("desc")
        )
        start = (page - 1) * page_size
        keep = (
            "account_id",
            "operation_id",
            "operation_date",
            "amount_minor",
            "direction",
            "counterparty_inn",
            "counterparty_name",
            "payment_purpose",
            "balance_eod_minor",
            "category",
        )
        return {
            "items": [{k: tx[k] for k in keep} for tx in selected[start : start + page_size]],
            "page": page,
            "page_size": page_size,
            "total": len(selected),
            "totals": {
                "count": len(selected),
                "inflow_minor": sum(t["amount_minor"] for t in selected if t["direction"] == "in"),
                "outflow_minor": sum(
                    t["amount_minor"] for t in selected if t["direction"] == "out"
                ),
            },
            "facets": {
                "categories": [
                    {
                        "code": code,
                        "label": CATEGORY_LABELS.get(code, code),
                        "group": category_group(code),
                        "count": count,
                    }
                    for code, count in sorted(categories.items(), key=lambda kv: (-kv[1], kv[0]))
                ],
                "counterparties": [
                    {"name": name, "inn": inn, **values}
                    for (name, inn), values in sorted(
                        counterparties.items(), key=lambda kv: -kv[1]["count"]
                    )[:20]
                ],
            },
        }

    @staticmethod
    def _operation_match(tx, f) -> bool:
        if f.get("direction") and tx["direction"] != f["direction"]:
            return False
        if f.get("category") and tx["category"] not in f["category"]:
            return False
        if f.get("group") and category_group(tx["category"]) not in f["group"]:
            return False
        if f.get("counterparty_inn") and tx["counterparty_inn"] != f["counterparty_inn"]:
            return False
        if f.get("amount_min") is not None and tx["amount_minor"] < f["amount_min"]:
            return False
        if f.get("amount_max") is not None and tx["amount_minor"] > f["amount_max"]:
            return False
        return not f.get("q") or _matches(
            f["q"], tx["counterparty_name"], tx["counterparty_inn"], tx["payment_purpose"]
        )
