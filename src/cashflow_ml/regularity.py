import calendar
import hashlib
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from cashflow_domain.schemas import CATEGORY_LABELS, PlannedEvent

RECIPE_VERSION = "regular-v1"


def component_key(tx: dict) -> str:
    # Stable, history-derived grouping. Unknown and financing are intentionally excluded.
    text = f"{tx['category']}|{tx['direction']}|{tx['counterparty_inn'] or tx['counterparty_name']}"
    return "component:" + hashlib.sha256(text.encode()).hexdigest()[:16]


def detect_components(transactions: list[dict], as_of: date) -> dict:
    groups = defaultdict(list)
    for tx in transactions:
        day = date.fromisoformat(tx["operation_date"])
        if as_of - timedelta(days=180) <= day <= as_of and tx["category"] not in {
            "unknown",
            "owner_funding",
            "own_transfer",
            "refund",
            "loan_in",
            "owner_withdrawal",
        }:
            groups[component_key(tx)].append(tx)
    components = {}
    for key, txs in groups.items():
        # Multiple same-day operations are one observation, not spurious repeated events.
        by_day = defaultdict(int)
        for tx in txs:
            by_day[tx["operation_date"]] += tx["amount_minor"]
        days = sorted(date.fromisoformat(d) for d in by_day)
        if len(days) < 3:
            continue
        gaps = [(b - a).days for a, b in zip(days, days[1:], strict=False)]
        period = median(gaps)
        amounts = list(by_day.values())
        amount = int(median(amounts))
        stable_amount = median(abs(a - amount) for a in amounts) / max(amount, 1) <= 0.35
        if not stable_amount:
            continue
        rhythm = next(
            (
                name
                for name, lo, hi in [("weekly", 6, 8), ("fortnight", 12, 16), ("monthly", 25, 35)]
                if lo <= period <= hi and sum(lo <= g <= hi for g in gaps) / len(gaps) >= 0.7
            ),
            None,
        )
        if not rhythm or (as_of - days[-1]).days > 1.8 * period:
            continue
        components[key] = dict(
            key=key,
            rhythm=rhythm,
            last=days[-1],
            month_day=int(median(d.day for d in days)),
            amount_minor=amount,
            category=txs[-1]["category"],
            direction=txs[-1]["direction"],
        )
    return components


def predicted_events(components: dict, account_id: str, as_of: date) -> list[PlannedEvent]:
    result = []
    end = as_of + timedelta(days=30)
    for c in components.values():
        cursor = c["last"]
        while cursor <= end:
            if c["rhythm"] == "monthly":
                year, month = cursor.year + (cursor.month == 12), cursor.month % 12 + 1
                cursor = date(year, month, min(c["month_day"], calendar.monthrange(year, month)[1]))
            else:
                cursor += timedelta(days=7 if c["rhythm"] == "weekly" else 14)
            if as_of < cursor <= end:
                result.append(
                    PlannedEvent(
                        id=f"regular:{account_id}:{c['key']}:{cursor}",
                        account_id=account_id,
                        component_id=c["key"],
                        category=c["category"],
                        amount_minor=c["amount_minor"],
                        direction=c["direction"],
                        due_date=cursor,
                        expected_date=cursor,
                        known_at=as_of,
                        source="regular",
                        status="assumption",
                        label=f"{CATEGORY_LABELS[c['category']]} · повтор по истории",
                    )
                )
    return result
