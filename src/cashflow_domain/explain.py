"""Template explanation of a liquidity gap for a business owner. No LLM: every number
comes from the saved forecast or scenario calculation."""

from collections import defaultdict
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from cashflow_domain.schemas import (
    CATEGORY_LABELS,
    ForecastResult,
    GapAction,
    GapExplanation,
    PlannedEvent,
)

SUPPLIER_CATEGORIES = frozenset(
    {"supplier", "operating_expense", "contractor", "logistics", "marketing"}
)
LOW_INFLOW_RATIO = Decimal("0.9")


def fmt_money(minor: int, up: bool = False) -> str:
    """340 тыс. ₽ / 1,2 млн ₽ / 850 ₽. `up` rounds a deficit up so it is not understated."""
    rub = Decimal(minor) / 100
    rounding = ROUND_CEILING if up else ROUND_HALF_UP
    if rub >= 999_500:
        value = (rub / 1_000_000).quantize(Decimal("0.01"), rounding)
        return f"{str(value).replace('.', ',')} млн ₽"
    if rub >= 10_000:
        return f"{(rub / 1000).quantize(Decimal(1), rounding)} тыс. ₽"
    if rub >= 1000:
        value = (rub / 1000).quantize(Decimal("0.1"), rounding)
        return f"{str(value).replace('.', ',')} тыс. ₽"
    return f"{rub.quantize(Decimal(1), rounding)} ₽"


def fmt_day(day: date) -> str:
    return day.strftime("%d.%m")


def _active(events: list[PlannedEvent], as_of: date, until: date, direction: str):
    return [
        e
        for e in events
        if e.direction == direction
        and as_of < e.expected_date <= until
        and e.known_at <= as_of
        and e.status not in {"cancelled", "paid", "review"}
        and e.remaining_minor > 0
    ]


def explain_gap(
    base: ForecastResult, points, events, risk, changes: list[str] | None = None
) -> GapExplanation:
    """`points`, `events`, `risk` come from the base forecast or from a scenario over it."""
    gap_date = risk.first_gap_date
    if gap_date is None:
        low = min(points, key=lambda p: p.closing_balance_minor)
        reserve = low.closing_balance_minor - risk.threshold_minor
        text = (
            f"Разрыва нет. Минимальный остаток {fmt_money(low.closing_balance_minor)} "
            f"на {fmt_day(low.date)}, запас до порога {fmt_money(max(0, reserve))}."
        )
        return GapExplanation(
            has_gap=False,
            gap_date=None,
            shortfall_minor=0,
            when="разрыва нет",
            shortfall=fmt_money(0),
            why=list(changes or []),
            actions=[],
            text=text,
        )

    shortfall = risk.liquidity_need_minor
    outs = _active(events, base.as_of, gap_date, "out")
    by_category: dict[str, list[PlannedEvent]] = defaultdict(list)
    for e in outs:
        by_category[e.category].append(e)
    ranked = sorted(by_category.items(), key=lambda kv: -sum(e.remaining_minor for e in kv[1]))
    why = [f"Сценарий: {c}" for c in (changes or [])]
    if risk.current_gap_minor:
        why.append(
            f"остаток на дату среза {fmt_money(base.opening_balance_minor)} ниже порога "
            f"{fmt_money(risk.threshold_minor)} на {fmt_money(risk.current_gap_minor, up=True)}"
        )
    for category, items in ranked[:3]:
        biggest = max(items, key=lambda e: e.remaining_minor)
        total = sum(e.remaining_minor for e in items)
        label = CATEGORY_LABELS.get(category, category)
        when = (
            fmt_day(biggest.expected_date)
            if len(items) == 1
            else f"{len(items)} пл., с {fmt_day(min(e.expected_date for e in items))}"
        )
        why.append(f"{label} {fmt_money(total)} ({when})")
    window = [p for p in points if p.date <= gap_date]
    resid_out = sum(p.residual_out_minor for p in window)
    if resid_out:
        why.append(f"прочие расходы по среднему потоку {fmt_money(resid_out)}")
    incoming = sum(p.event_in_minor + p.residual_in_minor for p in window)
    recent = [d["inflow_minor"] for d in base.history[-30:] if d.get("valid", True)]
    if recent:
        usual = Decimal(sum(recent)) / len(recent) * len(window)
        if Decimal(incoming) < usual * LOW_INFLOW_RATIO:
            why.append(
                f"поступления до {fmt_day(gap_date)} ниже обычного: {fmt_money(incoming)} "
                f"против {fmt_money(int(usual))} за такой же срок в последние 30 дней"
            )

    actions = _actions(base, events, risk, gap_date, shortfall, outs)
    when_text = fmt_day(gap_date) + (" (уже на дату среза)" if risk.current_gap_minor else "")
    shortfall_text = fmt_money(shortfall, up=True)
    text = (
        f"Когда: {when_text}. Не хватит: {shortfall_text}. "
        f"Почему: {'; '.join(why) if why else 'расходы превышают остаток и поступления'}. "
        f"Что сделать: {' '.join(f'{i}) {a.text}' for i, a in enumerate(actions, 1))}"
    )
    return GapExplanation(
        has_gap=True,
        gap_date=gap_date,
        shortfall_minor=shortfall,
        when=when_text,
        shortfall=shortfall_text,
        why=why,
        actions=actions,
        text=text,
    )


def _actions(base, events, risk, gap_date, shortfall, outs) -> list[GapAction]:
    actions = []
    movable = [e for e in outs if e.category in SUPPLIER_CATEGORIES]
    if movable:
        e = max(movable, key=lambda e: e.remaining_minor)
        covered = min(100, e.remaining_minor * 100 // max(shortfall, 1))
        actions.append(
            GapAction(
                code="postpone_supplier",
                amount_minor=e.remaining_minor,
                text=(
                    f"Перенести платёж поставщику «{e.label}» {fmt_money(e.remaining_minor)} "
                    f"с {fmt_day(e.expected_date)} на срок после {fmt_day(gap_date)}: "
                    f"закрывает {covered}% дефицита."
                ),
            )
        )
    later = [
        e
        for e in events
        if e.direction == "in"
        and e.expected_date > gap_date
        and e.known_at <= base.as_of
        and e.status not in {"cancelled", "paid", "review"}
        and e.remaining_minor > 0
    ]
    if later:
        e = max(later, key=lambda e: e.remaining_minor)
        who = e.counterparty_name or e.label
        actions.append(
            GapAction(
                code="speed_up_client",
                amount_minor=e.remaining_minor,
                text=(
                    f"Ускорить оплату или запросить предоплату у клиента {who}: "
                    f"{fmt_money(e.remaining_minor)} ожидается {fmt_day(e.expected_date)}, "
                    f"после даты разрыва."
                ),
            )
        )
    else:
        actions.append(
            GapAction(
                code="speed_up_client",
                text="Запросить предоплату у крупных клиентов: в календаре нет поступлений после даты разрыва.",
            )
        )
    actions.append(
        GapAction(
            code="credit_line",
            amount_minor=shortfall,
            text=f"Оформить овердрафт или кредитную линию не менее {fmt_money(shortfall, up=True)}.",
        )
    )
    reserve = base.reserve_minor
    if reserve and not base.credit_limit_minor and shortfall <= reserve:
        text = (
            f"Порог включает резерв {fmt_money(reserve)}; дефицит {fmt_money(shortfall, up=True)} "
            "меньше резерва, поэтому резерв можно временно использовать и затем восстановить."
        )
    else:
        text = f"Сформировать резерв не менее {fmt_money(shortfall, up=True)}, чтобы такие периоды проходить без кредита."
    actions.append(GapAction(code="reserve", amount_minor=shortfall, text=text))
    return actions
