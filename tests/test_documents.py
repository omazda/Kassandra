from datetime import date, timedelta

import pytest

from cashflow_ml.calendar_ru import is_working_day
from cashflow_ml.documents import (
    DelayConfig,
    DocumentUniverse,
    build_contract_cycles,
    build_documents,
    extract_payments,
    parse_purpose,
)


def tx(op_date, amount, purpose, direction="in", category="customer_payment", inn="111", op="x"):
    return {
        "operation_id": op,
        "operation_date": str(op_date),
        "amount_minor": amount,
        "direction": direction,
        "category": category,
        "counterparty_inn": inn,
        "counterparty_name": "Клиент",
        "payment_purpose": purpose,
    }


def test_parse_invoice_with_contract_and_date():
    p = parse_purpose("Оплата по договору № 104/18 по счету 2183 от 01.04.2026 за товар. НДС")
    assert p.contract_number == "104/18"
    assert p.document.kind == "invoice"
    assert (p.document.number, p.document.doc_date) == ("2183", date(2026, 4, 1))


@pytest.mark.parametrize(
    ("text", "kind", "number", "day"),
    [
        ("Оплата по УПД № 1384 от 14.08.2024 за запчасти", "upd", "1384", date(2024, 8, 14)),
        (
            "Оплата за груз согласно счету №325 от 17.10.2024г. Без НДС",
            "invoice",
            "325",
            date(2024, 10, 17),
        ),
        (
            "Оплата по договору № 18/24 от 25.11.2025 за работы по акту КС-2 № 18 от 30.12.2025",
            "act",
            "18",
            date(2025, 12, 30),
        ),
    ],
)
def test_parse_document_kinds(text, kind, number, day):
    ref = parse_purpose(text).document
    assert (ref.kind, ref.number, ref.doc_date) == (kind, number, day)


def test_undated_and_personal_account_are_not_documents():
    assert parse_purpose("Оплата по счету № 3453 за ремонт автомобиля").document is None
    assert parse_purpose("Оплата услуг связи по лицевому счету 7537964 за декабрь") is None
    # «расчет» содержит «счет», но документом не является
    settle = parse_purpose("Окончательный расчет по договору № 9/25")
    assert [r.kind for r in settle.refs] == ["contract"]
    assert parse_purpose("Эквайринг. Мерчант №2936768. Дата реестра 02.07.2026") is None


def test_prepayment_is_not_partial_and_return_is_excluded():
    assert parse_purpose("Предоплата по договору № 531-24 от 01.10.2024 на мебель").stage == (
        "prepayment"
    )
    assert parse_purpose("Доплата по УПД № 191 от 09.06.2025").stage == "topup"
    assert parse_purpose("Возврат платежа: ошибка. Оплата по счету № 5 от 13.03.2026").stage == (
        "return"
    )


def make_universe(history_days=(10, 20, 30), config=None):
    """Один клиент: документы платятся ровно через history_days, затем открытый документ."""
    rows, start = [], date(2025, 1, 6)
    for i, delay in enumerate(history_days * 3):
        doc = start + timedelta(days=7 * i)
        pay = doc + timedelta(days=delay)
        rows.append(
            tx(pay, 100_00 * (i + 1), f"Оплата по счету № {i + 1} от {doc:%d.%m.%Y}", op=f"h{i}")
        )
    return rows


def test_registry_cuts_by_as_of_and_merges_partial_payments():
    rows = [
        tx("2025-03-05", 600, "Частичная оплата по УПД № 7 от 01.02.2025", op="a"),
        tx("2025-03-20", 400, "Доплата по УПД № 7 от 01.02.2025", op="b"),
        tx("2025-03-25", 500, "Оплата по счету № 8 от 10.04.2025", op="c"),
    ]
    docs = build_documents(extract_payments(rows, "A"))
    assert len(docs) == 2
    upd = next(d for d in docs if d.ref.number == "7")
    assert upd.payments == [(date(2025, 3, 5), 600), (date(2025, 3, 20), 400)]


def test_future_payments_do_not_leak_into_expected_date():
    base = make_universe()

    def registry(shift_days):
        rows = list(base)
        # открытый документ с оплатой после as_of; его фактическая дата сдвигается
        rows.append(
            tx(
                date(2025, 8, 1) + timedelta(days=shift_days),
                777_00,
                "Оплата по счету № 99 от 01.07.2025",
                op="open",
            )
        )
        payments = extract_payments(rows, "A")
        uni = DocumentUniverse(
            payments, {"A": "Опт"}, DelayConfig(min_client=3, min_industry=3, min_global=3)
        )
        return uni.registry("A", date(2025, 7, 20))

    first, second = registry(0), registry(40)
    assert [e.date for e in first] == [e.date for e in second]
    assert first[0].amount_minor == second[0].amount_minor == 777_00
    assert first[0].source == "documents"
    assert first[0].known_at == date(2025, 7, 1)
    assert is_working_day(first[0].date)
    assert first[0].date > date(2025, 7, 20)


def test_delay_uses_only_history_before_as_of():
    rows = make_universe()
    payments = extract_payments(rows, "A")
    cfg = DelayConfig(min_client=3, min_industry=3, min_global=3, condition=False)
    uni = DocumentUniverse(payments, {"A": "Опт"}, cfg)
    early = date(2025, 2, 20)
    est = uni.estimate_delay(uni._delay, "A", "in", "111", date(2025, 2, 10), early)
    assert est.level in ("fallback", "client")  # к 20.02 мало завершённых оплат
    later = date(2025, 9, 1)
    est2 = uni.estimate_delay(uni._delay, "A", "in", "111", date(2025, 8, 20), later)
    assert est2.level == "client"
    assert est2.median_days == 20


def test_condition_on_unpaid_age_shifts_expected_delay():
    rows = make_universe()
    payments = extract_payments(rows, "A")
    base = dict(min_client=3, min_industry=3, min_global=3)
    uni = DocumentUniverse(payments, None, DelayConfig(condition=True, **base))
    as_of, doc = date(2025, 9, 1), date(2025, 8, 5)  # возраст 27 дней: задержка 10 и 20 исключены
    est = uni.estimate_delay(uni._delay, "A", "in", "111", doc, as_of)
    assert est.median_days >= 27


def test_contract_prepay_to_settlement_uses_history_ratio_and_lag():
    rows = []
    for i in range(4):  # закрытые договоры: расчёт = 2 x предоплата через 30 дней
        pre = date(2025, 1, 10) + timedelta(days=20 * i)
        rows.append(
            tx(
                pre,
                100_00,
                f"Предоплата по договору № {i}/25 от {pre:%d.%m.%Y}",
                category="contract_prepayment",
                op=f"p{i}",
            )
        )
        fin = pre + timedelta(days=30)
        rows.append(
            tx(
                fin,
                200_00,
                f"Окончательный расчет по договору № {i}/25 от {pre:%d.%m.%Y}",
                category="contract_settlement",
                op=f"f{i}",
            )
        )
    pre = date(2025, 8, 1)
    rows.append(
        tx(
            pre,
            50_00,
            "Предоплата по договору № 9/25 от 01.08.2025",
            category="contract_prepayment",
            op="p9",
        )
    )
    rows.append(
        tx(
            pre + timedelta(days=31),
            123_00,
            "Окончательный расчет по договору № 9/25",
            category="contract_settlement",
            op="f9",
        )
    )
    payments = extract_payments(rows, "A")
    cycles = build_contract_cycles(payments)
    assert sum(c.settle is not None for c in cycles) == 5
    cfg = DelayConfig(min_client=3, min_industry=3, min_global=3)
    uni = DocumentUniverse(payments, None, cfg)
    events = uni.registry("A", date(2025, 8, 15))
    assert len(events) == 1
    ev = events[0]
    assert ev.mechanism == "contract_settlement" and ev.category == "contract_settlement"
    assert (
        ev.amount_minor == 100_00
    )  # 50_00 x медианное отношение 2; будущая 123_00 не используется
    assert ev.known_at == date(2025, 8, 1)
    assert abs((ev.date - date(2025, 8, 31)).days) <= 4


def test_owned_payments_and_event_to_planned_event():
    rows = make_universe() + [tx("2025-08-10", 500_00, "Оплата по счету № 50 от 01.07.2025")]
    uni = DocumentUniverse(
        extract_payments(rows, "A"), None, DelayConfig(min_client=3, min_industry=3, min_global=3)
    )
    owned = uni.owned_payments("A", date(2025, 7, 20))
    assert (date(2025, 8, 10), 500_00, "in", "customer_payment") in owned
    event = uni.registry("A", date(2025, 7, 20))[0]
    planned = event.to_planned_event("A")
    assert planned.amount_minor == event.amount_minor and planned.expected_date == event.date
    assert planned.known_at == event.known_at


def test_document_after_as_of_is_not_in_registry():
    rows = [tx("2025-08-10", 500_00, "Оплата по счету № 50 от 01.08.2025")]
    uni = DocumentUniverse(extract_payments(rows, "A"))
    assert uni.registry("A", date(2025, 7, 31)) == []
    assert len(uni.registry("A", date(2025, 8, 1))) == 1
    assert uni.registry("A", date(2025, 8, 10)) == []  # оплачен в as_of: уже в истории
