import pytest
from conftest import OWNER, csv_bytes, transaction

from cashflow_domain.schemas import (
    CATEGORY_LABELS,
    FINANCING_CATEGORIES,
    REVENUE_CATEGORIES,
    Category,
    is_financing,
    is_revenue,
)
from cashflow_ml.features import flow_frame
from cashflow_services.categories import (
    RULES,
    categorize,
    categorize_detail,
    counterparty_template,
    purpose_template,
)
from cashflow_services.data import parse_transactions

BANK = "ПАО «Банк»"

# Defects of the dataset (docs/ml_protocol.md) plus the mechanisms of the generator.
CASES = [
    # acquiring: the text contains "возврат покупки" and must not become a refund
    (
        "Зачисление средств по операциям эквайринга. Мерчант №123. Дата реестра 01.03.2026. "
        "Сумма операций 10000.00, комиссия 190.00, возврат покупки 0.00. НДС не облагается",
        "in",
        BANK,
        "acquiring",
    ),
    # "Без налога (НДС)" is a VAT note, not a tax
    (
        "Оплата по УПД № 12 от 01.02.2026. Без налога (НДС)",
        "in",
        "ООО «Магазин-12»",
        "customer_payment",
    ),
    (
        "Оплата по счету № 5 от 01.02.2026 за товар. НДС не облагается",
        "in",
        "ООО «Кафе-3»",
        "customer_payment",
    ),
    # SBP: the text contains "комиссия"
    (
        "Зачисление по операциям оплаты через СБП (QR-код) за 01.03.2026. Сумма 500.00, "
        "комиссия 2.00. НДС не облагается",
        "in",
        BANK,
        "sbp",
    ),
    ("Пополнение счета собственными средствами", "in", "ИП Предприниматель-5", "owner_funding"),
    ("Выдача кредита (транш) по договору кредитной линии № КЛ-7", "in", BANK, "loan_in"),
    (
        "Окончательный расчет по договору № 77 от 05.03.2026",
        "in",
        "Физическое лицо",
        "contract_settlement",
    ),
    (
        "Предоплата по договору № 77 от 05.03.2026 на изготовление мебели",
        "in",
        "Физическое лицо",
        "contract_prepayment",
    ),
    (
        "Перечисление денежных средств по договору оферты № 4 за период с 01.03.2026 по 07.03.2026",
        "in",
        "ООО «Маркетплейс-2»",
        "marketplace_payout",
    ),
    ("Перевод на личный счет предпринимателя", "out", "ИП Предприниматель-5", "owner_withdrawal"),
    (
        "Перевод собственных средств ИП на счет физического лица",
        "out",
        "ИП Предприниматель-5",
        "owner_withdrawal",
    ),
    (
        "Перевод собственных средств на расчетный счет в АО «Банк2»",
        "out",
        "ИП Предприниматель-5",
        "own_transfer",
    ),
    (
        "Перевод собственных средств с расчетного счета в АО «Банк2»",
        "in",
        "ИП Предприниматель-5",
        "own_transfer",
    ),
    (
        "Взнос наличных через устройство самообслуживания № 9. Торговая выручка",
        "in",
        "ИП Предприниматель-5",
        "cash_in",
    ),
    ("Инкассированная выручка по сумке № 55 от 01.03.2026", "in", BANK, "cash_in"),
    (
        "Предоставление беспроцентного займа учредителем по договору № 1/2",
        "in",
        "Учредитель (физическое лицо)",
        "owner_funding",
    ),
    (
        "Возврат беспроцентного займа учредителю по договору № 1/2",
        "out",
        "Учредитель (физическое лицо)",
        "owner_withdrawal",
    ),
    (
        "Выплата дивидендов за 1 квартал 2026 г. согласно решению участника. НДФЛ удержан",
        "out",
        "Участник общества (физическое лицо)",
        "owner_withdrawal",
    ),
    (
        "Перечисление заработной платы (аванс) за март 2026 г. по реестру № 5 от 20.03.2026",
        "out",
        BANK,
        "salary",
    ),
    (
        "Аванс по заработной плате за март 2026 г. Перечисление на счет сотрудника",
        "out",
        "Сотрудник",
        "salary",
    ),
    ("Единый налоговый платеж", "out", "Казначейство России (ФНС России)", "tax"),
    (
        "Списание по поручению налогового органа № 5 от 01.03.2026. Взыскание задолженности по ЕНП",
        "out",
        "Казначейство России (ФНС России)",
        "tax",
    ),
    (
        "Оплата аренды нежилого помещения по договору № 3/4 за март 2026 г",
        "out",
        "ООО «Арендодатель-3»",
        "rent",
    ),
    (
        "Оплата за электроэнергию по договору № 8 за март 2026 г",
        "out",
        "ПАО «Энергосбыт-1»",
        "utilities",
    ),
    ("Оплата по счету № 8 за вывоз ТКО", "out", "ООО «Региональный оператор ТКО-2»", "utilities"),
    (
        "Оплата услуг связи и доступа в Интернет по лицевому счету 123 за март 2026 г",
        "out",
        "ПАО «Телеком-4»",
        "telecom",
    ),
    (
        "Оплата по счету № 3 за предоставление права использования ПО (подписка) за март 2026 г",
        "out",
        "ООО «Учетные системы-1»",
        "software",
    ),
    (
        "Оплата по счету № 9 от 01.03.2026 за бухгалтерские услуги",
        "out",
        "ООО «Бухгалтерия-Аутсорс-1»",
        "accounting",
    ),
    (
        "Оплата по счету № 9 от 01.03.2026 за услуги охраны",
        "out",
        "ООО «Охранное предприятие-1»",
        "facility_services",
    ),
    (
        "Оплата по счету № 9 от 01.03.2026 за услуги уборки",
        "out",
        "ООО «Клининг-1»",
        "facility_services",
    ),
    (
        "Оплата по счету № 9 от 01.03.2026 за мясную продукцию",
        "out",
        "ООО «Мясокомбинат-1»",
        "supplier",
    ),
    (
        "Пополнение рекламного кабинета по договору-оферте",
        "out",
        "ООО «Рекламная платформа-1»",
        "marketing",
    ),
    (
        "Оплата транспортно-экспедиционных услуг по счету № 4",
        "out",
        "ООО «Транспортная компания-1»",
        "logistics",
    ),
    (
        "Погашение кредита по договору № КД-5 от 01.01.2026: основной долг 1000, проценты 50",
        "out",
        BANK,
        "debt_service",
    ),
    (
        "Лизинговый платеж № 3 по договору лизинга № ЛД-5 от 01.01.2026",
        "out",
        "АО «Лизинговая компания-1»",
        "debt_service",
    ),
    ("Комиссия за ведение счета за март 2026 г. Без НДС", "out", BANK, "bank_fee"),
    ("Плата за аренду POS-терминалов за март 2026 г", "out", BANK, "bank_fee"),
    (
        "Возврат платежа: неверно указаны реквизиты получателя. Оплата по счету № 5",
        "in",
        "ООО «Мясокомбинат-1»",
        "refund",
    ),
    ("Оплата услуг по договору ГПХ, чек № 5", "out", "Физическое лицо (самозанятый)", "contractor"),
]


@pytest.mark.parametrize(("purpose", "direction", "counterparty", "expected"), CASES)
def test_plan_v3_defects_and_mechanisms(purpose, direction, counterparty, expected):
    assert categorize(purpose, direction, counterparty) == expected


def test_old_signature_and_generic_keywords_still_work():
    assert categorize("Перевод собственных средств", "in") == "own_transfer"
    assert categorize("Взнос учредителя", "in") == "owner_funding"
    assert categorize("Оплата услуг", "in") == "revenue"
    assert categorize("", "in") == "unknown"
    assert categorize("ЕНП", "out") == "tax"


def test_empty_purpose_uses_counterparty():
    assert categorize("", "out", "Казначейство России (ФНС России)") == "tax"
    assert categorize("", "out", "Сотрудник") == "salary"
    assert categorize("", "in", BANK) == "unknown"
    assert categorize("", "in", "ИП Предприниматель-5") == "unknown"


def test_tax_words_in_inflow_note_do_not_make_tax():
    # An inflow is never a tax payment, even if the text mentions a tax.
    assert categorize("Оплата по счету № 1. Налог на добавленную стоимость", "in") != "tax"


def test_rule_ids_are_unique_and_categories_known():
    ids = [r.rule_id for r in RULES]
    assert len(ids) == len(set(ids))
    assert {r.category for r in RULES} <= set(CATEGORY_LABELS)
    assert set(CATEGORY_LABELS) == {c.value for c in Category}
    assert categorize_detail("Единый налоговый платеж", "out")[1] == "tax"
    assert categorize_detail("xyz", "out") == ("unknown", "default")


def test_templates():
    assert counterparty_template("ООО «Магазин-128»") == "ООО «Магазин-»"
    assert purpose_template("Оплата по УПД № 279 от 19.08.2024") == "Оплата по УПД № # от #.#.#"


def test_revenue_and_financing_are_disjoint():
    assert not REVENUE_CATEGORIES & FINANCING_CATEGORIES
    for category in ("acquiring", "sbp", "cash_in", "customer_payment", "marketplace_payout"):
        assert is_revenue(category, "in") and not is_financing(category, "in")
    for category in ("owner_funding", "loan_in", "own_transfer"):
        assert is_financing(category, "in") and not is_revenue(category, "in")
    # Outflows are never revenue or financing.
    assert not is_revenue("acquiring", "out") and not is_financing("own_transfer", "out")


def test_revenue_stream_collects_all_operating_revenue_and_no_financing():
    rows = [
        ("acq", "Зачисление средств по операциям эквайринга. Мерчант №1", "Поступление", BANK),
        (
            "sbp",
            "Зачисление по операциям оплаты через СБП (QR-код) за 01.03.2026",
            "Поступление",
            BANK,
        ),
        ("cash", "Инкассированная выручка по сумке № 5", "Поступление", BANK),
        (
            "inv",
            "Оплата по УПД № 5 от 01.02.2026. Без налога (НДС)",
            "Поступление",
            "ООО «Магазин-1»",
        ),
        ("mp", "Перечисление по договору оферты № 5", "Поступление", "ООО «Маркетплейс-1»"),
        ("own", "Пополнение счета собственными средствами", "Поступление", "ИП Предприниматель-1"),
        ("loan", "Выдача кредита (транш) по договору кредитной линии № КЛ-1", "Поступление", BANK),
        (
            "tr",
            "Перевод собственных средств с расчетного счета в АО «Банк2»",
            "Поступление",
            "ИП Предприниматель-1",
        ),
    ]
    payload = csv_bytes(
        [
            {**transaction(operation=op, purpose=p, direction=d), "counterparty_name": c}
            for op, p, d, c in rows
        ]
    )
    txs = parse_transactions(payload)
    from datetime import date

    frame = flow_frame(txs, date(2026, 1, 1), date(2026, 1, 1), {})
    assert frame["revenue"].sum() == 5 * 10001
    assert frame["other_in"].sum() == 3 * 10001


def test_revenue_drop_scales_new_revenue_categories_and_keeps_financing(svc):
    from datetime import date

    from cashflow_domain.schemas import EventCreate, ForecastRequest, PlannedEvent, ScenarioSpec

    def event(key, category):
        return PlannedEvent(
            id=key,
            account_id="A",
            component_id="manual",
            category=category,
            amount_minor=100000,
            direction="in",
            due_date=date(2026, 5, 10),
            expected_date=date(2026, 5, 10),
            known_at=date(2026, 4, 30),
            source="manual",
            status="confirmed",
        )

    for key in ("acquiring", "customer_payment", "loan_in", "owner_funding"):
        svc.add_event(EventCreate(event=event(key, key)), OWNER)
    base = svc.forecast(
        ForecastRequest(account_id="A", as_of=date(2026, 4, 30), model="structural")
    )
    scenario = svc.evaluate_scenario(
        ScenarioSpec(base_run_id=base.run_id, kind="revenue_drop", revenue_drop_pct=100)
    )
    kept = {e.id for e in scenario.events}
    assert {"acquiring", "customer_payment"}.isdisjoint(kept)
    assert {"loan_in", "owner_funding"} <= kept


def test_import_assigns_new_categories(svc):
    payload = csv_bytes(
        [
            {
                **transaction(operation="z1", purpose="Взнос наличных № 5"),
                "counterparty_name": "ИП Предприниматель-1",
            }
        ]
    )
    svc.import_transactions(payload, OWNER)
    stored = {t["operation_id"]: t["category"] for t in svc.store.transactions("A")}
    assert stored["z1"] == "cash_in"
