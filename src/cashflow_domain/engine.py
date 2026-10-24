from datetime import timedelta
from decimal import Decimal

from cashflow_domain.money import model_minor
from cashflow_domain.schemas import (
    FIXED_COST_CATEGORIES,
    REVENUE_CATEGORIES,
    ForecastPoint,
    ForecastResult,
    PlannedEvent,
    RiskResult,
    ScenarioResult,
    ScenarioSpec,
)


def assemble(as_of, opening: int, residuals, events: list[PlannedEvent]) -> list[ForecastPoint]:
    if len(residuals) != 30:
        raise ValueError("Требуется ровно 30 дневных потоков")
    points = []
    balance = opening
    for h, (revenue, other, outgoing) in enumerate(residuals, start=1):
        day = as_of + timedelta(days=h)
        active = [
            e
            for e in events
            if e.expected_date == day
            and e.known_at <= as_of
            and e.status not in {"cancelled", "paid", "review"}
        ]
        incoming_events = sum(e.remaining_minor for e in active if e.direction == "in")
        outgoing_events = sum(e.remaining_minor for e in active if e.direction == "out")
        revenue, other, outgoing = (model_minor(v) for v in (revenue, other, outgoing))
        incoming = revenue + other
        balance += incoming_events - outgoing_events + incoming - outgoing
        points.append(
            ForecastPoint(
                date=day,
                event_in_minor=incoming_events,
                event_out_minor=outgoing_events,
                residual_in_minor=incoming,
                residual_out_minor=outgoing,
                residual_revenue_minor=revenue,
                closing_balance_minor=balance,
            )
        )
    return points


# Confirmed events from these sources describe flows the model already expects on average.
NETTING_SOURCES = frozenset(
    {"contract", "invoice", "tax", "bank", "accounting", "marketplace", "ofd"}
)
# One owner of a flow: a known event displaces only the matching part of the model's stream.
FLOW_GROUPS = {
    "customer_payment": "clients",
    "contract_prepayment": "clients",
    "contract_settlement": "clients",
    "revenue": "clients",
    "marketplace_payout": "marketplace",
    "acquiring": "cards",
    "sbp": "cards",
    "cash_in": "cash",
    "supplier": "suppliers",
    "contractor": "suppliers",
    "operating_expense": "suppliers",
    "logistics": "suppliers",
    "marketing": "suppliers",
    "rent": "fixed",
    "utilities": "fixed",
    "telecom": "fixed",
    "software": "fixed",
    "accounting": "fixed",
    "facility_services": "fixed",
    "bank_fee": "fixed",
    "salary": "salary",
    "tax": "tax",
    "debt_service": "debt",
}
STREAM_NAMES = ("revenue", "other_in", "out")


def stream_of(category: str, direction: str) -> int:
    """Index of the model stream (features.flow_frame): operating revenue, other inflow, outflow."""
    if direction == "out":
        return 2
    return 0 if category in REVENUE_CATEGORIES else 1


def flow_shares(transactions: list[dict], as_of, days: int = 90) -> dict:
    """Share of each flow group inside its model stream over the last `days` up to as_of."""
    since = str(as_of - timedelta(days=days))
    totals, streams = {}, [0, 0, 0]
    for tx in transactions:
        if not since < tx["operation_date"] <= str(as_of):
            continue
        stream = stream_of(tx["category"], tx["direction"])
        group = FLOW_GROUPS.get(tx["category"], "other")
        streams[stream] += tx["amount_minor"]
        totals[(stream, group)] = totals.get((stream, group), 0) + tx["amount_minor"]
    return {key: value / streams[key[0]] for key, value in totals.items() if streams[key[0]]}


def net_known_events(residuals, events: list[PlannedEvent], as_of, shares: dict):
    """Known future flows replace the part of the statistical forecast that stands for them.

    For every flow group the displaced amount is min(known events, group share × stream total),
    taken proportionally from every day of the stream. Known money above the group's average
    stays additional: it is genuinely more than usual.
    """
    rows = [list(map(float, row)) for row in residuals]
    end = as_of + timedelta(days=len(rows))
    known = {}
    for e in events:
        if (
            e.source in NETTING_SOURCES
            and e.decision == "additional"
            and as_of < e.expected_date <= end
            and e.known_at <= as_of
            and e.status not in {"cancelled", "paid", "review"}
        ):
            key = (stream_of(e.category, e.direction), FLOW_GROUPS.get(e.category, "other"))
            known[key] = known.get(key, 0) + e.remaining_minor
    if not known:
        return rows, None
    totals = [sum(r[s] for r in rows) for s in range(3)]
    displaced, groups = [0.0, 0.0, 0.0], []
    for (stream, group), amount in sorted(known.items()):
        capacity = shares.get((stream, group), 0.0) * totals[stream]
        taken = min(float(amount), capacity)
        displaced[stream] += taken
        groups.append(
            {
                "stream": STREAM_NAMES[stream],
                "group": group,
                "events_minor": int(amount),
                "capacity_minor": int(round(capacity)),
                "displaced_minor": int(round(taken)),
            }
        )
    factors = [max(0.0, 1 - displaced[s] / totals[s]) if totals[s] > 0 else 1.0 for s in range(3)]
    rows = [[row[s] * factors[s] for s in range(3)] for row in rows]
    return rows, {
        "factors": factors,
        "displaced_minor": int(round(sum(displaced))),
        "groups": groups,
        "rule": "Известные события вытесняют свою долю среднего потока модели (доля группы за 90 дней)",
    }


def deterministic_risk(opening: int, points, reserve=0, credit=0, limitation="") -> RiskResult:
    threshold = reserve - credit
    breached = [p for p in points if p.closing_balance_minor < threshold]
    minimum = min(p.closing_balance_minor for p in points)
    return RiskResult(
        threshold_minor=threshold,
        current_gap_minor=max(0, threshold - opening),
        new_risk=opening >= threshold and bool(breached),
        first_gap_date=breached[0].date if breached else None,
        minimum_balance_minor=minimum,
        liquidity_need_minor=max(0, threshold - min(minimum, opening)),
        calibration_status="unavailable",
        probability_source="Нет пригодного банка ошибок",
        limitation=limitation
        or "Вероятность недоступна; точечная траектория не гарантирует отсутствие разрыва. Один счёт не отражает всю ликвидность бизнеса.",
    )


def fixed_cost_share(transactions: list[dict], as_of, categories=None, days: int = 90) -> Decimal:
    """Share of fixed costs in operating outflow over the `days` before as_of (inclusive).

    Uses only operations dated <= as_of. Owner withdrawals and own transfers are not costs.
    """
    fixed = set(categories or FIXED_COST_CATEGORIES)
    since = str(as_of - timedelta(days=days))
    total = part = 0
    for tx in transactions:
        if tx["direction"] != "out" or not (since < tx["operation_date"] <= str(as_of)):
            continue
        if tx["category"] in {"own_transfer", "owner_withdrawal"}:
            continue
        total += tx["amount_minor"]
        if tx["category"] in fixed:
            part += tx["amount_minor"]
    return Decimal(part) / Decimal(total) if total else Decimal(0)


def _client_matches(event: PlannedEvent, key: str) -> bool:
    key = key.strip().casefold()
    return key in {
        (event.counterparty_inn or "").strip().casefold(),
        (event.counterparty_name or "").strip().casefold(),
    }


def _future_open(event: PlannedEvent, as_of) -> bool:
    return event.expected_date > as_of and event.status not in {"paid", "cancelled", "review"}


def _apply(part, base, events, residuals, assumptions, context):
    if part.kind == "delay":
        event = next((e for e in events if e.id == part.event_id), None)
        if not event or event.direction != "in" or not _future_open(event, base.as_of):
            raise ValueError("Доступное будущее поступление не найдено")
        event.expected_date += timedelta(days=part.delay_days)
        assumptions.append(
            f"Поступление {event.id} перенесено на {event.expected_date}; за горизонтом оно остаётся в реестре."
        )
    elif part.kind == "client_delay":
        moved = [
            e
            for e in events
            if e.direction == "in"
            and _future_open(e, base.as_of)
            and _client_matches(e, part.counterparty)
        ]
        if not moved:
            raise ValueError("У этого клиента нет будущих поступлений в календаре")
        for e in moved:
            e.expected_date += timedelta(days=part.delay_days)
        total = sum(e.remaining_minor for e in moved)
        assumptions.append(
            f"Клиент {part.counterparty}: {len(moved)} поступлений на {total} коп. перенесены на "
            f"{part.delay_days} дн.; остаток за горизонтом остаётся в реестре. "
            "Остаточный поток выручки не менялся: сдвигаются только события реестра и повторов."
        )
    elif part.kind == "revenue_drop":
        factor = (Decimal(100) - Decimal(str(part.revenue_drop_pct))) / 100
        for row in residuals:
            row[0] = model_minor(Decimal(row[0]) * factor)
        for e in events:
            if e.category in REVENUE_CATEGORIES and e.direction == "in":
                # Only the unpaid part of an event is conditional future revenue.
                e.amount_minor = e.paid_minor + model_minor(Decimal(e.remaining_minor) * factor)
        events[:] = [e for e in events if e.amount_minor > 0]
        assumptions.append(
            "Снижена только операционная выручка; финансирование и собственные переводы сохранены."
        )
    elif part.kind == "fixed_cost_increase":
        start = part.start_date or base.as_of + timedelta(days=1)
        categories = (
            {c.value for c in part.categories} if part.categories else set(FIXED_COST_CATEGORIES)
        )
        share = context.get("fixed_share")
        if share is None:
            raise ValueError("Не передана доля фиксированных расходов из истории")
        up = Decimal(1) + Decimal(str(part.fixed_cost_pct)) / 100
        touched = 0
        for e in events:
            if (
                e.direction == "out"
                and e.category in categories
                and e.expected_date >= start
                and e.expected_date > base.as_of
                and e.status not in {"paid", "cancelled", "review"}
            ):
                e.amount_minor = e.paid_minor + model_minor(Decimal(e.remaining_minor) * up)
                touched += 1
        resid_up = Decimal(1) + Decimal(str(part.fixed_cost_pct)) / 100 * share
        for h, row in enumerate(residuals, start=1):
            if base.as_of + timedelta(days=h) >= start:
                row[2] = model_minor(Decimal(row[2]) * resid_up)
        names = ", ".join(sorted(categories))
        assumptions.append(
            f"Фиксированные расходы ({names}) выросли на {part.fixed_cost_pct:g}% с {start}: "
            f"изменено событий {touched}. Остаточный отток вырос пропорционально доле "
            f"фиксированных расходов в оттоке за 90 дней до среза: {float(share) * 100:.1f}%."
        )
    else:
        if part.expense_date <= base.as_of:
            raise ValueError("Расход сценария должен быть после as_of")
        event_id = f"scenario:{context['scenario_id']}"
        if any(e.id == event_id for e in events):
            event_id += f":{len(events)}"
        events.append(
            PlannedEvent(
                id=event_id,
                account_id=base.account_id,
                component_id="scenario_extra",
                category="operating_expense",
                amount_minor=part.amount_minor,
                direction="out",
                due_date=part.expense_date,
                expected_date=part.expense_date,
                known_at=base.as_of,
                source="manual",
                status="confirmed",
                label="Внеплановый расход сценария",
            )
        )
        assumptions.append(
            f"Добавлен внеплановый расход {part.amount_minor} коп. на {part.expense_date}."
        )


def scenario(
    base: ForecastResult, spec: ScenarioSpec, scenario_id: str, context: dict | None = None
) -> ScenarioResult:
    """Pure what-if over a saved forecast. `context` carries history-derived inputs
    (`fixed_share`: Decimal) computed by the service from operations dated <= as_of."""
    context = {**(context or {}), "scenario_id": scenario_id}
    events = [e.model_copy(deep=True) for e in base.events]
    residuals = [
        [
            p.residual_revenue_minor,
            p.residual_in_minor - p.residual_revenue_minor,
            p.residual_out_minor,
        ]
        for p in base.points
    ]
    assumptions = ["Условный сценарий; история и сохранённый базовый план не изменены."]
    parts = spec.parts if spec.kind == "combined" else [spec]
    for part in parts:
        _apply(part, base, events, residuals, assumptions, context)
    if spec.kind == "combined":
        assumptions.append(
            f"Комбинированный сценарий: {len(parts)} негативных изменения применены одновременно."
        )
    points = assemble(base.as_of, base.opening_balance_minor, residuals, events)
    risk = deterministic_risk(
        base.opening_balance_minor,
        points,
        base.reserve_minor,
        base.credit_limit_minor,
        "Для условного сценария вероятность не калибрована и не переносится из базового прогноза.",
    )
    return ScenarioResult(
        scenario_id=scenario_id,
        base_run_id=base.run_id,
        spec=spec,
        points=points,
        events=events,
        risk=risk,
        delta_end_minor=points[-1].closing_balance_minor - base.points[-1].closing_balance_minor,
        delta_liquidity_minor=risk.liquidity_need_minor - base.risk.liquidity_need_minor,
        base_first_gap_date=base.risk.first_gap_date,
        assumptions=assumptions,
    )
