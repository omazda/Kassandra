from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

Minor = Annotated[StrictInt, Field(ge=0, le=10**16)]
PositiveMinor = Annotated[StrictInt, Field(gt=0, le=10**16)]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Category(StrEnum):
    REVENUE = "revenue"
    OPERATING = "operating_expense"
    TAX = "tax"
    SALARY = "salary"
    RENT = "rent"
    DEBT = "debt_service"
    OWNER = "owner_funding"
    TRANSFER = "own_transfer"
    REFUND = "refund"
    BANK = "bank_fee"
    UNKNOWN = "unknown"
    ACQUIRING = "acquiring"
    SBP = "sbp"
    CASH_IN = "cash_in"
    CUSTOMER_PAYMENT = "customer_payment"
    MARKETPLACE_PAYOUT = "marketplace_payout"
    CONTRACT_PREPAYMENT = "contract_prepayment"
    CONTRACT_SETTLEMENT = "contract_settlement"
    OTHER_INCOME = "other_income"
    LOAN_IN = "loan_in"
    OWNER_WITHDRAWAL = "owner_withdrawal"
    SUPPLIER = "supplier"
    CONTRACTOR = "contractor"
    UTILITIES = "utilities"
    TELECOM = "telecom"
    SOFTWARE = "software"
    ACCOUNTING = "accounting"
    FACILITY_SERVICES = "facility_services"
    MARKETING = "marketing"
    LOGISTICS = "logistics"
    LEGAL_CLAIM = "legal_claim"


CATEGORY_LABELS = {
    "revenue": "Выручка",
    "operating_expense": "Основные расходы",
    "tax": "Налоги",
    "salary": "Зарплата",
    "rent": "Аренда",
    "debt_service": "Обслуживание долга",
    "owner_funding": "Финансирование владельцем",
    "own_transfer": "Собственные переводы",
    "refund": "Возвраты",
    "bank_fee": "Банковские комиссии",
    "unknown": "Не определено",
    "acquiring": "Эквайринг",
    "sbp": "СБП",
    "cash_in": "Инкассация и взнос наличных",
    "customer_payment": "Оплата клиентов",
    "marketplace_payout": "Выплаты маркетплейса",
    "contract_prepayment": "Предоплата по договору",
    "contract_settlement": "Окончательный расчёт по договору",
    "other_income": "Прочие доходы (субсидии, страховые выплаты)",
    "loan_in": "Кредит и заём (поступление)",
    "owner_withdrawal": "Выводы владельца",
    "supplier": "Поставщики и закупки",
    "contractor": "Подрядчики и самозанятые",
    "utilities": "Коммунальные услуги",
    "telecom": "Связь",
    "software": "ПО и учётные системы",
    "accounting": "Бухгалтерия (аутсорс)",
    "facility_services": "Охрана и клининг",
    "marketing": "Реклама",
    "logistics": "Логистика и топливо",
    "legal_claim": "Судебные взыскания",
}

# Operating revenue: only these inflows feed the revenue stream and the revenue-drop scenario.
REVENUE_CATEGORIES = frozenset(
    {
        "revenue",
        "acquiring",
        "sbp",
        "cash_in",
        "customer_payment",
        "marketplace_payout",
        "contract_prepayment",
        "contract_settlement",
    }
)
# External money that closes a gap (D2 label): never revenue. own_transfer counts only inbound.
FINANCING_CATEGORIES = frozenset({"owner_funding", "loan_in", "own_transfer"})


# Fixed monthly costs (D5): the default scope of the fixed_cost_increase scenario.
FIXED_COST_CATEGORIES = (
    "rent",
    "utilities",
    "telecom",
    "software",
    "accounting",
    "facility_services",
    "bank_fee",
)


def is_revenue(category: str, direction: str) -> bool:
    return direction == "in" and category in REVENUE_CATEGORIES


def is_financing(category: str, direction: str) -> bool:
    return direction == "in" and category in FINANCING_CATEGORIES


# Источники ожиданий, которые строит система (не пользователь): их можно замещать.
ASSUMPTION_SOURCES = frozenset({"regular", "tax_calendar", "documents"})

EventSource = Literal[
    "regular",
    "tax_calendar",
    "documents",
    "manual",
    "csv",
    "contract",
    "invoice",
    "tax",
    "bank",
    "accounting",
    "marketplace",
    "ofd",
]
# Shown to the user next to every planned movement: where the expectation came from.
SOURCE_LABELS = {
    "regular": "Повтор по истории",
    "tax_calendar": "Налоговый календарь (расчёт)",
    "documents": "Счета и договоры из выписки",
    "manual": "Ручной ввод",
    "csv": "Импорт реестра",
    "contract": "Договор",
    "invoice": "Счёт",
    "tax": "Налоговый платёж (подтверждён)",
    "bank": "Банк: кредиты, зарплатный проект, платёжки",
    "accounting": "1С / учётная система",
    "marketplace": "Маркетплейс",
    "ofd": "Онлайн-касса (ОФД)",
}
# For badges and filter chips where the full label does not fit.
SOURCE_SHORT_LABELS = {
    "regular": "По истории",
    "tax_calendar": "Налоги (расчёт)",
    "documents": "Из выписки",
    "manual": "Вручную",
    "csv": "Реестр",
    "contract": "Договор",
    "invoice": "Счёт",
    "tax": "Налог",
    "bank": "Банк",
    "accounting": "1С",
    "marketplace": "Маркетплейс",
    "ofd": "Касса",
}
Certainty = Literal["promised", "agreed", "documented"]
CERTAINTY_LABELS = {
    "promised": "Обещание (устная договорённость)",
    "agreed": "Подписан договор",
    "documented": "Выставлен счёт / платёжный документ",
}


class PlannedEvent(Schema):
    id: str = Field(min_length=1, max_length=160)
    account_id: str
    component_id: str
    category: Category
    amount_minor: PositiveMinor
    direction: Literal["in", "out"]
    due_date: date
    expected_date: date
    known_at: date
    source: EventSource
    status: Literal["assumption", "confirmed", "partially_paid", "paid", "cancelled", "review"]
    paid_minor: Minor = 0
    replacement_id: str | None = None
    decision: Literal["replace", "additional"] = "additional"
    label: str = Field(default="Плановое событие", max_length=240)
    # Optional document/counterparty data (older stored events simply lack them).
    counterparty_inn: str | None = Field(default=None, max_length=32)
    counterparty_name: str | None = Field(default=None, max_length=240)
    document_ref: str | None = Field(default=None, max_length=160)
    # How firm the user's expectation is; informational, the forecast treats it as confirmed.
    certainty: Certainty | None = None

    @model_validator(mode="after")
    def valid_payment(self):
        if self.paid_minor > self.amount_minor:
            raise ValueError("Оплата превышает сумму события")
        if self.decision == "replace" and not self.replacement_id:
            raise ValueError("Для замещения нужен replacement_id")
        return self

    @property
    def remaining_minor(self) -> int:
        return self.amount_minor - self.paid_minor


class EventCreate(Schema):
    event: PlannedEvent
    base_run_id: str | None = None


class PaymentLink(Schema):
    operation_id: str
    amount_minor: PositiveMinor
    known_at: date


class ForecastRequest(Schema):
    account_id: str
    as_of: date
    horizon: Literal[30] = 30
    model: Literal["constant", "mean", "weekday", "hgb", "structural", "ensemble"] = "hgb"
    model_version: str | None = None
    calendar: bool = True
    reserve_minor: Minor = 0
    credit_limit_minor: Minor = 0
    data_version: int | None = None
    event_version: int | None = None
    # Только structural: ретроспективный реестр документов знает будущие оплаты (утечка, бэктест).
    retro_registry: bool = False


class ForecastPoint(Schema):
    date: date
    event_in_minor: Minor
    event_out_minor: Minor
    residual_in_minor: Minor
    residual_out_minor: Minor
    residual_revenue_minor: Minor
    closing_balance_minor: int
    p10_minor: int | None = None
    p90_minor: int | None = None


class StressExcluded(TypedDict):
    event_id: str
    label: str
    expected_date: str
    amount_minor: int
    reason: str


class Stress(TypedDict):
    description: str
    excluded: list[StressExcluded]
    excluded_minor: int
    first_gap_date: str | None
    liquidity_need_minor: int
    minimum_balance_minor: int


class NettingGroup(TypedDict):
    stream: str
    group: str
    events_minor: int
    capacity_minor: int
    displaced_minor: int


class Netting(TypedDict):
    factors: list[float]
    displaced_minor: int
    groups: list[NettingGroup]
    rule: str


class OverdueEvent(TypedDict):
    event_id: str
    label: str
    source: str
    direction: str
    expected_date: str
    remaining_minor: int
    days_overdue: int
    treatment: Literal["rolled_forward", "excluded"]
    new_date: str | None
    note: str


class RiskResult(Schema):
    threshold_minor: int
    current_gap_minor: Minor
    new_risk: bool
    first_gap_date: date | None
    minimum_balance_minor: int
    liquidity_need_minor: Minor
    probability_30d: float | None = Field(default=None, ge=0, le=1)
    calibration_status: Literal[
        "unavailable",
        "experimental_uncalibrated",
        "calibrated_on_jan_feb",
        "calibrated_on_selection",
    ]
    probability_source: str
    # X11: the calibrated probability reached the alarm threshold (None: no calibrated probability).
    alarm: bool | None = None
    trajectory_count: int = 0
    simulations: int = 0
    limitation: str
    # Downside view: the same forecast without promised or risky receipts (None when there are none).
    stress: Stress | None = None


class ForecastResult(Schema):
    run_id: str
    account_id: str
    as_of: date
    model: str
    model_version: str
    trained_to: date | None
    data_version: int
    event_version: int
    calendar: bool
    opening_balance_minor: int
    reserve_minor: Minor
    credit_limit_minor: Minor
    points: list[ForecastPoint] = Field(min_length=30, max_length=30)
    events: list[PlannedEvent]
    risk: RiskResult
    quality_flags: list[str]
    confidence: dict | None = None
    latest_operation_date: date
    history: list[dict]
    created_at: str
    # How known events replaced the model's average flows (None: nothing was replaced).
    netting: Netting | None = None
    # Registered events whose date passed before as_of and what the forecast did with them.
    overdue_events: list[OverdueEvent] = Field(default_factory=list)


PartKind = Literal["delay", "revenue_drop", "extra_expense", "fixed_cost_increase", "client_delay"]


class ScenarioParams(Schema):
    event_id: str | None = None
    delay_days: int = Field(default=14, ge=1, le=365)
    revenue_drop_pct: float = Field(default=20, ge=0, le=100)
    amount_minor: Minor = 0
    expense_date: date | None = None
    fixed_cost_pct: float = Field(default=10, ge=0, le=300)
    start_date: date | None = None
    categories: list[Category] | None = None
    counterparty: str | None = Field(default=None, max_length=240)


class ScenarioPart(ScenarioParams):
    """One negative change inside a combined scenario."""

    kind: PartKind

    @model_validator(mode="after")
    def complete(self):
        _check_params(self)
        return self


def _check_params(p):
    if p.kind == "delay" and not p.event_id:
        raise ValueError("Выберите поступление")
    if p.kind == "extra_expense" and (not p.amount_minor or not p.expense_date):
        raise ValueError("Укажите положительную сумму и дату расхода")
    if p.kind == "client_delay":
        if not (p.counterparty and p.counterparty.strip()):
            raise ValueError("Укажите клиента (ИНН или название)")
        if not 1 <= p.delay_days <= 60:
            raise ValueError("Задержка клиента: от 1 до 60 дней")
    if p.kind == "fixed_cost_increase" and p.categories is not None and not p.categories:
        raise ValueError("Список категорий фиксированных расходов пуст")


class ScenarioSpec(ScenarioParams):
    base_run_id: str
    kind: Literal[
        "delay", "revenue_drop", "extra_expense", "fixed_cost_increase", "client_delay", "combined"
    ]
    parts: list[ScenarioPart] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def complete(self):
        if self.kind == "combined":
            if len(self.parts) < 2:
                raise ValueError("Комбинированный сценарий: нужно не менее двух изменений")
            kinds = [p.kind for p in self.parts]
            if len(set(kinds)) != len(kinds) and any(
                kinds.count(k) > 1 for k in ("revenue_drop", "fixed_cost_increase")
            ):
                raise ValueError("Снижение выручки и рост расходов можно указать один раз")
        else:
            if self.parts:
                raise ValueError("parts допустимы только для combined")
            _check_params(self)
        return self


class GapAction(Schema):
    code: Literal["postpone_supplier", "speed_up_client", "credit_line", "reserve"]
    text: str
    amount_minor: Minor = 0


class GapExplanation(Schema):
    has_gap: bool
    gap_date: date | None
    shortfall_minor: Minor
    when: str
    shortfall: str
    why: list[str]
    actions: list[GapAction]
    text: str


class ScenarioResult(Schema):
    scenario_id: str
    base_run_id: str
    spec: ScenarioSpec
    points: list[ForecastPoint]
    events: list[PlannedEvent]
    risk: RiskResult
    delta_end_minor: int
    delta_liquidity_minor: int
    base_first_gap_date: date | None
    assumptions: list[str]
