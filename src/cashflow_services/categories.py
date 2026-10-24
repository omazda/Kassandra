"""Rule-based categorization of bank operations.

A rule is a pair (counterparty pattern, purpose pattern) plus a direction; the first matching
rule in RULES wins, so the order below is the explicit priority. Purpose rules go from the most
specific mechanism (acquiring, SBP, financing) to generic keywords; counterparty-only rules
close the list and handle empty or unusual purposes.
"""

import re
import unicodedata
from dataclasses import dataclass

# Boilerplate that mentions a tax but does not make the operation a tax payment.
_VAT_NOTE = re.compile(
    r"без налога \(ндс\)|без ндс|ндс не облагается|(?:в том числе|в т\.ч\.)?\s*\bндс\b.*$"
)


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: str
    direction: str | None = None
    counterparty: str | None = None
    purpose: str | None = None

    def matches(self, direction: str, counterparty: str, purpose: str) -> bool:
        return (
            (self.direction is None or self.direction == direction)
            and (
                self.counterparty is None or re.search(self.counterparty, counterparty) is not None
            )
            and (self.purpose is None or re.search(self.purpose, purpose) is not None)
        )


def normalize_purpose(purpose: str) -> str:
    s = unicodedata.normalize("NFKC", purpose or "").lower().replace("ё", "е")
    return _VAT_NOTE.sub(" ", s)


def normalize_counterparty(name: str) -> str:
    s = unicodedata.normalize("NFKC", name or "").lower().replace("ё", "е")
    return re.sub(r"\s+", " ", re.sub(r"\d+", "", s)).strip()


def counterparty_template(name: str) -> str:
    """Counterparty name without digits (pattern key for rules and reports)."""
    return re.sub(r"\s+", " ", re.sub(r"\d+", "", name or "")).strip()


def purpose_template(purpose: str) -> str:
    """Purpose with digit groups replaced by # (pattern key for rules and reports)."""
    return re.sub(r"\d+", "#", purpose or "").strip()


_ORG = r"^(ооо|ао|пао|зао|ип) "

RULES: tuple[Rule, ...] = (
    # Revenue mechanisms and financing inflows. Order matters: acquiring text contains
    # "возврат покупки" and SBP text contains "комиссия", which must not trigger refund/fee.
    Rule("acquiring", "acquiring", "in", purpose=r"эквайринг"),
    Rule("sbp", "sbp", "in", purpose=r"\bсбп\b|система быстрых платежей|qr-код"),
    Rule(
        "cash_in",
        "cash_in",
        "in",
        purpose=r"инкассир|взнос наличн|внесение наличн|прием наличн|выручка по сумке",
    ),
    Rule(
        "marketplace",
        "marketplace_payout",
        "in",
        purpose=r"договор\w* оферт|маркетплейс|агентск\w+ договор",
    ),
    Rule(
        "loan_in",
        "loan_in",
        "in",
        purpose=r"выдача кредита|выдача займа|\bтранш|предоставление кредита|получение кредита",
    ),
    Rule(
        "owner_funding",
        "owner_funding",
        "in",
        purpose=(
            r"учредител|владельц|уставн\w+ капитал|собственными средствами|финансов\w+ помощ"
            r"|договор\w* займа|беспроцентн\w+ займ|пополнение (расчетного )?счета"
        ),
    ),
    # Owner withdrawals must precede own_transfer ("перевод собственных средств ИП на счет
    # физического лица") and refund ("возврат займа учредителю").
    Rule(
        "owner_withdrawal",
        "owner_withdrawal",
        "out",
        purpose=r"дивиденд|личн\w+ (счет|карт)|счет физическ\w+ лица|займа учредител|вывод средств",
    ),
    Rule(
        "own_transfer",
        "own_transfer",
        purpose=r"собственн\w* средств|между (своими )?счет|на свой счет|со своего счета",
    ),
    Rule("retention_return", "customer_payment", "in", purpose=r"возврат гарантийн"),
    Rule("other_income", "other_income", "in", purpose=r"субсиди|грант|страхов\w+ возмещени"),
    Rule(
        "own_transfer_closing",
        "own_transfer",
        purpose=r"остатк\w+ денежных средств|закрытием счета",
    ),
    Rule("refund", "refund", purpose=r"возврат|ошибочн|неверно указан"),
    # Outflow mechanisms.
    Rule(
        "tax",
        "tax",
        "out",
        purpose=(r"\bенп\b|единый налогов|\bенс\b|налог|страхов\w+ взнос|взыскани\w+ задолж"),
    ),
    Rule("legal_claim", "legal_claim", "out", purpose=r"исполнительн\w+ лист|судебн|штраф"),
    Rule(
        "salary",
        "salary",
        "out",
        purpose=(
            r"заработн|зарплат|аванс.*(сотруд|з/п)|окончательн\w* расчет.*(сотруд|реестр)"
            r"|выплата отпускных"
        ),
    ),
    Rule(
        "contractor",
        "contractor",
        "out",
        purpose=r"\bгпх\b|самозанят|субподряд|договор\w* подряда|выполненные работы|по акту",
    ),
    Rule(
        "debt_service",
        "debt_service",
        "out",
        purpose=(
            r"кредит|лизинг|погашен\w+ (основного|займа|долга)|процент\w+ (по|за пользование)"
            r"|овердрафт"
        ),
    ),
    Rule(
        "bank_fee",
        "bank_fee",
        "out",
        purpose=r"комисси|ведение счета|обслуживание счета|\bрко\b|pos-терминал|терминал",
    ),
    Rule("rent", "rent", "out", purpose=r"аренд"),
    Rule(
        "utilities",
        "utilities",
        "out",
        purpose=(
            r"электроэнерг|водоснабжен|водоотведен|вывоз тко|\bтко\b|теплоснабжен|отоплен"
            r"|газоснабжен|коммунальн"
        ),
    ),
    Rule("telecom", "telecom", "out", purpose=r"услуг\w* связи|интернет|телефони|мобильн\w+ связ"),
    Rule(
        "software",
        "software",
        "out",
        purpose=r"подписк|права использования|облачн|программн|лицензи|\b1с\b|хостинг",
    ),
    Rule("accounting", "accounting", "out", purpose=r"бухгалтер"),
    Rule("facility", "facility_services", "out", purpose=r"охран|уборк|клининг"),
    Rule("marketing", "marketing", "out", purpose=r"реклам|продвижен|таргет|маркетинг"),
    Rule(
        "logistics",
        "logistics",
        "out",
        purpose=(
            r"транспортн|экспедиц|перевозк|грузоперевоз|доставк|топливн\w+ карт|провоза"
            r"|фулфилмент"
        ),
    ),
    Rule(
        "supplier",
        "supplier",
        "out",
        purpose=(
            r"оплата|упд|счету|счет №|договор\w* (поставки|купли)|закупк|товар|продукци"
            r"|материал|оборудован|автомобил|запчаст|предоплат|окончательн\w+ расчет|аванс"
            r"|поставк"
        ),
    ),
    Rule("operating_expense", "operating_expense", "out", purpose=r"услуг|расход|работ"),
    # Inflows from customers: prepayment and final settlement are separate stages of a contract.
    Rule(
        "contract_prepayment",
        "contract_prepayment",
        "in",
        purpose=r"предоплат|авансов\w+ платеж|аванс по договору",
    ),
    Rule(
        "contract_settlement",
        "contract_settlement",
        "in",
        purpose=r"окончательн\w+ (расчет|платеж)",
    ),
    Rule(
        "customer_payment",
        "customer_payment",
        "in",
        purpose=r"упд|счету|счет №|договор|по акту|оплата (за|по)|доплат|частичная оплата",
    ),
    Rule(
        "revenue",
        "revenue",
        "in",
        purpose=r"оплат|выручк|реализац|поступлен.*продаж",
    ),
    # Counterparty-only rules: empty or unusual purposes.
    Rule("cp_tax", "tax", "out", counterparty=r"казначейств|фнс"),
    Rule("cp_salary", "salary", "out", counterparty=r"^сотрудник"),
    Rule("cp_rent", "rent", "out", counterparty=r"арендодател"),
    Rule("cp_utilities", "utilities", "out", counterparty=r"водоканал|энергосбыт|оператор тко"),
    Rule("cp_telecom", "telecom", "out", counterparty=r"телеком"),
    Rule("cp_software", "software", "out", counterparty=r"учетные системы|облачный провайдер"),
    Rule("cp_accounting", "accounting", "out", counterparty=r"бухгалтерия"),
    Rule("cp_facility", "facility_services", "out", counterparty=r"охранное|клининг"),
    Rule("cp_marketing", "marketing", "out", counterparty=r"рекламная платформа"),
    Rule(
        "cp_logistics",
        "logistics",
        "out",
        counterparty=r"транспортная компания|топливная компания|система взимания",
    ),
    Rule("cp_debt", "debt_service", "out", counterparty=r"лизингов"),
    Rule(
        "cp_owner_out", "owner_withdrawal", "out", counterparty=r"^(учредитель|участник общества)"
    ),
    Rule("cp_owner_in", "owner_funding", "in", counterparty=r"^учредитель"),
    Rule("cp_contractor", "contractor", "out", counterparty=r"самозанят"),
    Rule("cp_legal", "legal_claim", "out", counterparty=r"^(ооо )?«?истец"),
    # The owner's own counterparty and the bank itself with no text stay unknown.
    Rule("cp_owner_self", "unknown", counterparty=r"предприниматель|^пао «банк»"),
    Rule("cp_supplier", "supplier", "out", counterparty=_ORG),
    Rule("cp_customer", "customer_payment", "in", counterparty=_ORG + r"|^физическое лицо"),
)


def categorize_detail(purpose: str, direction: str, counterparty: str = "") -> tuple[str, str]:
    """Return (category, rule_id) for an operation; rule_id is "default" when nothing matched."""
    text = normalize_purpose(purpose)
    name = normalize_counterparty(counterparty)
    for rule in RULES:
        if rule.matches(direction, name, text):
            return rule.category, rule.rule_id
    return "unknown", "default"


def categorize(purpose: str, direction: str, counterparty: str = "") -> str:
    return categorize_detail(purpose, direction, counterparty)[0]
