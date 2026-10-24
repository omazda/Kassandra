"""Request schemas for data that is not in the bank statement: contracts, invoices, bank feeds,
marketplaces and online cash registers. Every item becomes a PlannedEvent on a concrete date."""

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from cashflow_domain.schemas import Category, Certainty, Minor, PositiveMinor, Schema


class Counterparty(Schema):
    counterparty_inn: str | None = Field(default=None, pattern=r"^(\d{10}|\d{12})$")
    counterparty_name: str | None = Field(default=None, max_length=240)


class FeedOptions(Schema):
    # The date the information became known; forecasts before it ignore the events.
    known_at: date | None = None
    # With a base forecast an item may replace the matching history-based expectation.
    base_run_id: str | None = None


class ScheduledPayment(Schema):
    date: date
    amount_minor: PositiveMinor


class ContractSpec(Counterparty, FeedOptions):
    contract_id: str = Field(min_length=1, max_length=80, pattern=r"^[\w./№ -]+$")
    title: str = Field(default="", max_length=160)
    direction: Literal["in", "out"]  # in: client pays us, out: we pay a supplier
    total_minor: PositiveMinor
    signed_date: date
    # Explicit schedule wins; otherwise prepayment + remainder after delivery and deferral.
    schedule: list[ScheduledPayment] = Field(default_factory=list, max_length=120)
    prepayment_pct: float = Field(default=0, ge=0, le=100)
    prepayment_days: int = Field(default=0, ge=0, le=365)
    delivery_date: date | None = None
    deferral_days: int = Field(default=0, ge=0, le=365)
    installments: int = Field(default=1, ge=1, le=60)
    installment_every_days: int = Field(default=30, ge=1, le=365)

    @model_validator(mode="after")
    def consistent(self):
        if self.schedule:
            if sum(p.amount_minor for p in self.schedule) != self.total_minor:
                raise ValueError("Сумма графика платежей не равна сумме договора")
        elif self.prepayment_pct < 100 and self.delivery_date is None:
            raise ValueError("Укажите график платежей или дату поставки для остатка")
        return self


class InvoiceSpec(Counterparty, FeedOptions):
    number: str = Field(min_length=1, max_length=40)
    direction: Literal["in", "out"]  # in: issued to a client, out: received from a supplier
    issue_date: date
    due_date: date | None = None
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    amount_minor: PositiveMinor
    paid_minor: Minor = 0
    # in: shift the expected date by the client's typical delay learnt from the history
    use_client_delay: bool = True

    @model_validator(mode="after")
    def consistent(self):
        if self.due_date is None and self.payment_terms_days is None:
            raise ValueError("Укажите срок оплаты: due_date или payment_terms_days")
        if self.due_date and self.due_date < self.issue_date:
            raise ValueError("Срок оплаты раньше даты счёта")
        if self.paid_minor >= self.amount_minor:
            raise ValueError("Счёт уже оплачен полностью")
        return self


class LoanPayment(Schema):
    date: date
    principal_minor: Minor
    interest_minor: Minor = 0

    @model_validator(mode="after")
    def positive(self):
        if self.principal_minor + self.interest_minor <= 0:
            raise ValueError("Платёж по кредиту должен быть положительным")
        return self


class Loan(Schema):
    contract: str = Field(min_length=1, max_length=80)
    lender: str = Field(default="Банк", max_length=160)
    schedule: list[LoanPayment] = Field(min_length=1, max_length=240)


class PayrollPayment(Schema):
    date: date
    amount_minor: PositiveMinor
    kind: Literal["advance", "salary", "bonus", "vacation"] = "salary"


class PaymentOrder(Counterparty):
    number: str = Field(min_length=1, max_length=40)
    date: date
    amount_minor: PositiveMinor
    purpose: str = Field(default="", max_length=210)


class BankFeed(FeedOptions):
    """Internal bank data: loan schedules, payroll project, created but not executed orders."""

    loans: list[Loan] = Field(default_factory=list, max_length=50)
    payroll: list[PayrollPayment] = Field(default_factory=list, max_length=120)
    payment_orders: list[PaymentOrder] = Field(default_factory=list, max_length=500)


class MarketplacePayout(Schema):
    date: date
    amount_minor: PositiveMinor
    report_ref: str = Field(default="", max_length=80)


class MarketplaceFeed(FeedOptions):
    marketplace: str = Field(min_length=1, max_length=80)
    payouts: list[MarketplacePayout] = Field(min_length=1, max_length=400)


class OfdDay(Schema):
    date: date
    card_minor: Minor = 0
    sbp_minor: Minor = 0
    cash_minor: Minor = 0


class OfdFeed(FeedOptions):
    """Online cash register receipts; card and SBP money reaches the account a working day later."""

    register_id: str = Field(default="ККТ", max_length=60)
    days: list[OfdDay] = Field(min_length=1, max_length=120)
    acquiring_fee_pct: float = Field(default=1.8, ge=0, le=10)
    settlement_working_days: int = Field(default=1, ge=0, le=5)


class EventText(Schema):
    text: str = Field(min_length=3, max_length=300)
    # The «today» the phrase is relative to: the forecast date in a historical replay.
    today: date
    account_id: str | None = None


class TaxApply(Schema):
    base_run_id: str


class EventDraft(Counterparty):
    direction: Literal["in", "out"] | None = None
    amount_minor: PositiveMinor | None = None
    expected_date: date | None = None
    category: str | None = None
    label: str = ""
    certainty: Certainty | None = None


class EventEdit(Counterparty):
    """Change of a registered event. Fields left out keep their value; a new revision is written,
    so forecasts made before `known_at` still see the old version."""

    known_at: date
    expected_date: date | None = None
    due_date: date | None = None
    amount_minor: PositiveMinor | None = None
    category: Category | None = None
    label: str | None = Field(default=None, min_length=1, max_length=240)
    certainty: Certainty | None = None


class AssumptionEdit(Schema):
    """Correction of a history-based expectation (regular payment, tax, document) of a forecast:
    `edit` registers the user's version instead of it, `skip` removes it from the forecast."""

    action: Literal["edit", "skip"]
    known_at: date
    expected_date: date | None = None
    amount_minor: PositiveMinor | None = None
    label: str | None = Field(default=None, min_length=1, max_length=240)
