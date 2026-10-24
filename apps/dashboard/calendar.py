"""Calendar form and presentation helpers; financial calculations stay in the domain."""

import json
import uuid
from datetime import date, timedelta

from cashflow_domain.money import to_minor
from cashflow_domain.schemas import ASSUMPTION_SOURCES
from cashflow_domain.schemas import SOURCE_LABELS as DOMAIN_SOURCE_LABELS

FIELD_LABELS = {
    "label": "Название",
    "amount_minor": "Сумма",
    "direction": "Направление",
    "category": "Категория",
    "due_date": "Срок оплаты",
    "expected_date": "Ожидаемая дата",
    "known_at": "Известно на дату",
    "account_id": "Компания и счёт",
}
SOURCE_LABELS = {
    "regular": "Механизм по истории",
    "tax_calendar": "Налоговый календарь",
    "documents": "Реестр документов",
    "manual": "Ручной реестр",
    "csv": "CSV-реестр",
}
STATUS_LABELS = {
    "assumption": "Предположение",
    "confirmed": "Подтверждено",
    "partially_paid": "Частичная оплата",
    "paid": "Оплачено",
    "cancelled": "Отменено",
    "review": "Требует проверки",
}


def api_error_message(detail):
    if isinstance(detail, str):
        return detail
    if not isinstance(detail, list):
        return "Не удалось выполнить действие. Проверьте введённые данные."
    messages = []
    for error in detail:
        field = str(error.get("loc", [""])[-1])
        title = FIELD_LABELS.get(field, field)
        kind = error.get("type", "")
        if kind == "missing":
            message = "заполните поле"
        elif "date" in kind:
            message = "укажите дату в формате ГГГГ-ММ-ДД"
        elif kind in {"greater_than", "greater_than_equal", "int_type", "int_parsing"}:
            message = "укажите положительную сумму с точностью до копейки"
        else:
            message = error.get("msg", "проверьте значение")
        messages.append(f"{title}: {message}")
    return "; ".join(messages)


def form_dates(as_of):
    day = parse_date(as_of, "Дата расчёта")
    return str(day), str(day + timedelta(days=15))


def parse_date(value, label):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}: укажите корректную дату") from exc


def matching_base(base, account_id, as_of):
    return bool(base and base["account_id"] == account_id and base["as_of"] == as_of)


def build_event_request(
    *,
    account_id,
    as_of,
    base,
    session_id,
    label,
    amount,
    direction,
    category,
    due,
    expected,
    known,
    replacement,
):
    if not account_id:
        raise ValueError("Выберите компанию и счёт")
    parse_date(as_of, "Дата расчёта")
    if not label or not label.strip():
        raise ValueError("Название: укажите название события")
    if len(label.strip()) > 240:
        raise ValueError("Название: не более 240 символов")
    try:
        amount_minor = to_minor(str(amount)) if amount is not None else 0
    except ValueError as exc:
        raise ValueError("Сумма: укажите положительную сумму с точностью до копейки") from exc
    if amount_minor <= 0:
        raise ValueError("Сумма: укажите положительную сумму")
    for value, title in [
        (due, "Срок оплаты"),
        (expected, "Ожидаемая дата"),
        (known, "Известно на дату"),
    ]:
        parse_date(value, title)
    if direction not in {"in", "out"}:
        raise ValueError("Направление: выберите поступление или списание")
    if not replacement:
        raise ValueError("Выберите замещение ожидания или новое дополнительное событие")
    target = None
    if replacement != "additional":
        if not matching_base(base, account_id, as_of):
            raise ValueError("Для замещения сначала рассчитайте прогноз для выбранных счёта и даты")
        target = next(
            (
                e
                for e in base["events"]
                if e["id"] == replacement and e["source"] in ASSUMPTION_SOURCES
            ),
            None,
        )
        if target is None:
            raise ValueError(
                "Выбранное ожидание уже замещено или недоступно. Выберите другое ожидание либо новое дополнительное событие"
            )
        if direction != target["direction"] or category != target["category"]:
            raise ValueError("Направление и категория должны совпадать с замещаемым ожиданием")
        if known < as_of:
            raise ValueError(
                "Для замещения дата знания должна быть не раньше даты базового прогноза"
            )
    values = {
        "account_id": account_id,
        "category": category,
        "amount_minor": amount_minor,
        "direction": direction,
        "due_date": due,
        "expected_date": expected,
        "known_at": known,
        "source": "manual",
        "status": "confirmed",
        "label": label.strip(),
        "replacement_id": target["id"] if target else None,
        "decision": "replace" if target else "additional",
    }
    # The same form in this browser session gets the same ID, including after refreshes
    # of the base forecast. A retry/double click cannot create a second cash movement.
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, session_id + json.dumps(values, sort_keys=True)))
    event = {
        **values,
        "id": event_id,
        "component_id": target["component_id"] if target else f"manual:{event_id}",
    }
    return {
        "event": event,
        "base_run_id": base["run_id"] if matching_base(base, account_id, as_of) else None,
    }


def inclusion(event, as_of, in_snapshot):
    if event["known_at"] > as_of:
        return "Известно позже даты расчёта"
    if event["status"] in {"paid", "cancelled", "review"}:
        return STATUS_LABELS[event["status"]]
    if event["expected_date"] <= as_of:
        return "Просрочено — нужна новая дата"
    if event["expected_date"] > str(date.fromisoformat(as_of) + timedelta(days=30)):
        return "За горизонтом 30 дней"
    return "В прогнозе" if in_snapshot else "Ожидает расчёта прогноза"


def calendar_view(base, effective, all_registered, account_id, as_of):
    valid_base = matching_base(base, account_id, as_of)
    snapshot = {e["id"]: e for e in base["events"]} if valid_base else {}
    # Use historical revisions/paid amounts on the cut; later-known *new* events
    # are visible as excluded rows, never injected into an earlier forecast.
    events = {e["id"]: e for e in all_registered}
    events.update(snapshot)
    events.update({e["id"]: e for e in effective})
    replaced = {e["replacement_id"] for e in effective if e.get("replacement_id")}
    rows, payments = [], []
    for event_id, event in events.items():
        if event_id in replaced:
            continue
        remaining = event["amount_minor"] - event.get("paid_minor", 0)
        row = {
            "date": event["expected_date"],
            "known_at": event["known_at"],
            "due_date": event["due_date"],
            "label": event["label"],
            "amount": remaining / 100,
            "direction": "Поступление" if event["direction"] == "in" else "Списание",
            # New integrations add sources; fall back to the shared labels, then to the code.
            "source": SOURCE_LABELS.get(
                event["source"], DOMAIN_SOURCE_LABELS.get(event["source"], event["source"])
            )
            + " / "
            + STATUS_LABELS[event["status"]],
            "inclusion": inclusion(
                event, as_of, event_id in snapshot and snapshot[event_id] == event
            ),
            "kind": event["source"],
            "id": event_id,
        }
        rows.append(row)
        if event["source"] not in ASSUMPTION_SOURCES:
            payments.append({"label": f"{event['label']} · {event_id}", "value": event_id})
    return sorted(rows, key=lambda row: (row["date"], row["id"])), payments


def save_message(event, as_of):
    state = inclusion(event, as_of, False)
    if state == "Ожидает расчёта прогноза":
        detail = "Событие попадёт в прогноз после обновления расчёта."
    else:
        detail = f"В текущий прогноз не включено: {state.lower()}."
    units, cents = divmod(event["amount_minor"], 100)
    money = f"{units:,}".replace(",", " ") + f",{cents:02d} ₽"
    direction = "Поступление" if event["direction"] == "in" else "Списание"
    return f"Событие сохранено на {event['expected_date']}. {direction}: {money}. {detail}"
