"""Response models of the API v1.1 endpoints: the OpenAPI contract the clients generate from."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from cashflow_domain.schemas import ForecastResult, PlannedEvent

Direction = Literal["in", "out"]


class View(BaseModel):
    # A key the code returns but the contract does not describe is a bug: fail loudly in tests.
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- dictionaries and history


class CategoryItem(View):
    code: str
    label: str
    group: str
    is_revenue: bool
    is_financing: bool
    is_fixed_cost: bool


class SourceItem(View):
    code: str
    label: str
    short_label: str
    kind: Literal["assumption", "confirmed"]


class CodeLabel(View):
    code: str
    label: str


class Dictionaries(View):
    categories: list[CategoryItem]
    sources: list[SourceItem]
    certainty: list[CodeLabel]
    groups: list[CodeLabel]


class CategoryRow(View):
    direction: Direction
    category: str
    label: str
    group: str
    amount_minor: int
    operations: int
    share: float


class GroupRow(View):
    direction: Direction
    group: str
    label: str
    amount_minor: int
    operations: int
    share: float


class CategorySummary(View):
    date_from: date
    date_to: date
    inflow_minor: int
    outflow_minor: int
    rows: list[CategoryRow]
    # The same totals by category group: the slices of the structure chart.
    groups: list[GroupRow]


class RecurringItem(View):
    component_id: str
    category: str
    label: str
    direction: Direction
    rhythm: Literal["weekly", "fortnight", "monthly"]
    rhythm_label: str
    amount_minor: int
    last_date: date
    counterparty_inn: str | None
    counterparty_name: str | None
    next_dates: list[date]


class RecurringView(View):
    as_of: date
    method: str
    components: list[RecurringItem]


class TaxRegimeView(View):
    regime: str
    confidence: float
    evidence: dict
    label: str


class TaxEventView(View):
    date: date
    amount_minor: int
    label: str
    method: str
    window: list[date] | None
    in_horizon_30d: bool
    replaces_event_id: str | None


class TaxCalendarView(View):
    as_of: date
    regime: TaxRegimeView
    rules: list[str]
    events: list[TaxEventView]
    note: str


# ---------------------------------------------------------------- register and imports


class ReplacedAssumption(View):
    event_id: str
    replaced_id: str
    label: str


class PaymentDate(View):
    number: str
    event_id: str
    level: Literal["client", "account", "due_date"]
    n: int
    median_days: int | None
    due_date: date
    expected_date: date
    overdue: bool


class SkippedPayment(View):
    date: date
    amount_minor: int
    stage: str


class RegisterResult(View):
    created: int
    revised: int
    unchanged: int
    replaced_assumptions: list[ReplacedAssumption]
    events: list[PlannedEvent]
    skipped_past_due: list[SkippedPayment] | None = None
    payment_dates: list[PaymentDate] | None = None
    warning: str | None = None


class OneCSource(View):
    format: Literal["1CClientBankExchange"]
    accounts: list[str]
    documents: int
    period: list[date]
    opening_minor: int
    closing_minor: int
    closing_check: Literal["ok", "mismatch"] | None


class ImportReport(View):
    inserted: int
    duplicates: int
    touched_accounts: list[str]
    accounts: list[str]
    reconciliation: dict[str, dict[str, int]]
    auto_refresh: list[dict]
    source: OneCSource | None = None


class ParseDraft(View):
    direction: Direction | None
    amount_minor: int | None
    expected_date: date | None
    category: str | None
    label: str
    certainty: Literal["promised", "agreed", "documented"] | None
    counterparty_name: str | None


class ParseResult(View):
    draft: ParseDraft
    missing: list[Literal["direction", "amount_minor", "expected_date"]]
    complete: bool
    notes: list[str]


# ---------------------------------------------------------------- open registries


class CounterpartyCheck(View):
    inn: str
    status: Literal["active", "reorganizing", "liquidating", "liquidated", "bankrupt", "unknown"]
    status_label: str
    risk: Literal["ok", "warning", "critical", "unknown"]
    name: str | None
    source: Literal["offline", "demo_registry", "dadata"]
    note: str | None
    checked_at: str
    cached: bool


class SuggestedScenario(View):
    kind: Literal["client_delay"]
    counterparty: str
    delay_days: int


class CounterpartyRiskItem(View):
    role: Literal["client", "supplier"]
    counterparty_inn: str
    counterparty_name: str
    amount_minor: int
    check: CounterpartyCheck
    suggested_scenario: SuggestedScenario | None = None


class CounterpartyRisks(View):
    as_of: date
    checked: int
    flagged: int
    counterparties: list[CounterpartyRiskItem]


# ---------------------------------------------------------------- refresh, alerts, sources


class RefreshPrevious(View):
    run_id: str
    as_of: date
    first_gap_date: date | None
    liquidity_need_minor: int


class RefreshChanges(View):
    gap_appeared: bool
    gap_resolved: bool
    gap_shift_days: int | None
    need_delta_minor: int
    minimum_delta_minor: int


class RefreshResult(View):
    mode: Literal["unchanged", "events_only", "events_full", "new_data"]
    elapsed_ms: float
    previous: RefreshPrevious
    changes: RefreshChanges
    forecast: ForecastResult


class AlertAction(View):
    type: Literal["explanation", "reschedule", "scenario", "stress"]
    run_id: str | None = None
    event_id: str | None = None
    scenario: SuggestedScenario | None = None


class Alert(View):
    kind: Literal[
        "current_deficit",
        "new_gap",
        "gap_moved",
        "gap_resolved",
        "gap_risk",
        "overdue_payment",
        "risky_counterparty",
        "stress_gap",
        "data_quality",
    ]
    severity: Literal["critical", "warning", "info"]
    date: date | None
    amount_minor: int
    title: str
    text: str
    action: AlertAction | None = None


class AlertsView(View):
    run_id: str | None
    as_of: date | None = None
    alerts: list[Alert]
    note: str | None = None


class StatementInfo(View):
    operations: int | None
    first_date: date | None
    last_date: date | None
    data_version: int
    label: str


class SourceCount(View):
    source: str
    label: str
    events: int
    amount_in_minor: int
    amount_out_minor: int


class SystemSource(View):
    source: str
    label: str
    note: str


class SyncInfo(View):
    provider: str
    last_sync: str
    count: int


class SourcesView(View):
    statement: StatementInfo
    registered_events: list[SourceCount]
    system_sources: list[SystemSource]
    syncs: list[SyncInfo]


# ---------------------------------------------------------------- plan-fact and reconciliation


class PlanFactRow(View):
    date: date
    planned_minor: int
    actual_minor: int | None
    delta_minor: int | None
    planned_in_minor: int
    planned_out_minor: int
    actual_in_minor: int | None
    actual_out_minor: int | None
    coverage: str
    deviation: bool


class MatchingReview(View):
    event_id: str
    candidate_operation_ids: list[str]
    status: Literal["needs_review"]
    reason: str


class OverduePayment(View):
    event_id: str
    label: str
    source: str
    direction: Direction
    expected_date: date
    days_overdue: int
    remaining_minor: int
    counterparty_name: str | None
    warning: str


class PlanFactSummary(View):
    days_compared: int
    deviation_threshold_minor: int
    deviation_days: int
    mean_abs_delta_minor: int | None
    max_abs_delta_minor: int | None
    latest_data_date: date


class PlanFactWeek(View):
    week_start: date
    week_end: date
    planned_in_minor: int
    planned_out_minor: int
    actual_in_minor: int
    actual_out_minor: int
    days: int
    days_with_fact: int
    planned_minor: int
    actual_minor: int | None
    complete: bool
    delta_minor: int | None
    deviation: bool


class PlanFactView(View):
    run_id: str
    data_version: int
    rows: list[PlanFactRow]
    matching_review: list[MatchingReview]
    overdue: list[OverduePayment]
    summary: PlanFactSummary
    granularity: Literal["day", "week"]
    weeks: list[PlanFactWeek] | None = None


class ReconcileLink(View):
    event_id: str
    label: str
    operation_id: str
    operation_date: date
    amount_minor: int
    fully_paid: bool
    score: int
    reasons: list[str]


class ReconcileCandidate(View):
    operation_id: str
    operation_date: date
    amount_minor: int
    score: int
    reasons: list[str]


class ReconcileReview(View):
    event_id: str
    label: str
    candidates: list[ReconcileCandidate]


class ReconcileResult(View):
    checked_events: int
    linked: list[ReconcileLink]
    needs_review: list[ReconcileReview]
    dry_run: bool


# ---------------------------------------------------------------- v1.0 endpoints, typed in v1.1


class Health(View):
    status: Literal["ok"]
    llm_enabled: bool


class Transition(View):
    previous_date: date
    date: date
    difference_minor: int


class AccountQuality(View):
    operations: int | None = None
    first_date: date | None = None
    last_date: date | None = None
    inconsistent_transitions: list[Transition] = []
    inconsistent_eod_days: list[date] = []
    suspicious_duplicates: list[list[str]] = []
    empty_purpose: int | None = None
    empty_inn: int | None = None
    calendar_days: int | None = None
    unconfirmed_no_operation_days: int | None = None
    invalid_days: int | None = None


class Account(View):
    id: str
    name: str
    owner: str
    demo: int
    data_version: int
    event_version: int
    quality: AccountQuality


class Transaction(View):
    account_id: str
    operation_id: str
    operation_date: date
    amount_minor: int
    direction: Direction
    counterparty_inn: str
    counterparty_name: str
    payment_purpose: str
    balance_eod_minor: int
    category: str


class FlowTotals(View):
    count: int
    inflow_minor: int
    outflow_minor: int


class CategoryFacet(View):
    code: str
    label: str
    group: str
    count: int


class CounterpartyFacet(View):
    name: str
    inn: str
    count: int
    amount_minor: int


class OperationFacets(View):
    categories: list[CategoryFacet]
    counterparties: list[CounterpartyFacet]


class TransactionsPage(View):
    items: list[Transaction]
    page: int
    page_size: int
    total: int
    totals: FlowTotals
    facets: OperationFacets


# ---------------------------------------------------------------- payment calendar


class CalendarItem(View):
    id: str
    date: date
    due_date: date
    direction: Direction
    amount_minor: int
    label: str
    title: str
    # Joins an expectation with GET /accounts/{id}/recurring (rhythm and usual day).
    component_id: str
    category: str
    category_label: str
    group: str
    source: str
    source_label: str
    source_short: str
    kind: Literal["assumption", "registered"]
    status: str
    certainty: str | None
    certainty_label: str | None
    counterparty_name: str | None
    counterparty_inn: str | None
    obligatory: bool
    overdue: bool
    actions: list[Literal["edit", "delete", "skip"]]


class FacetCount(View):
    code: str
    label: str
    count: int


class CalendarFacets(View):
    sources: list[FacetCount]
    categories: list[FacetCount]
    directions: list[FacetCount]
    obligatory: int
    cancelled: int


class CalendarView(View):
    run_id: str
    account_id: str
    as_of: date
    date_to: date
    items: list[CalendarItem]
    totals: FlowTotals
    facets: CalendarFacets


class DailyRow(View):
    date: date
    inflow_minor: int
    outflow_minor: int
    balance_minor: int
    coverage: str
    valid: bool
    operation_count: int


class CategoryUpdated(View):
    status: Literal["updated"]
    category: str


class CoverageGapResult(View):
    status: Literal["confirmed_missing"]
    start_date: date
    end_date: date


class ForecastSummary(View):
    run_id: str
    as_of: date
    model_version: str
    data_version: int
    event_version: int


class PaymentLinkResult(View):
    status: Literal["linked", "already_linked"]
    remaining_minor: int | None = None


class TopClient(View):
    counterparty_inn: str | None
    counterparty_name: str | None
    amount_minor: int
    operations: int
    last_date: date
    share: float
    upcoming_events: int | None = None
    upcoming_minor: int | None = None


# ---------------------------------------------------------------- debug datasets


class DebugRowError(View):
    line: int
    reason: str


class DebugQuality(View):
    inconsistent_transitions: int
    material_transitions: int
    inconsistent_eod_days: int
    suspicious_duplicate_groups: int
    empty_purpose: int
    empty_inn: int
    invalid_days: int


class DebugCategory(View):
    code: str
    label: str
    operations: int
    amount_minor: int


class DebugCategories(View):
    unknown_operations: int
    unknown_share: float
    top: list[DebugCategory]


class DebugAccount(View):
    account_id: str
    company_id: str
    data_from: date | None = None
    history_copied: int = 0
    name: str
    operations: int
    first_date: date
    last_date: date
    last_balance_minor: int
    inconsistent_transitions: int
    suspicious_duplicates: int
    invalid_days: int
    unknown_share: float


class DebugDatasetSummary(View):
    id: str
    filename: str
    created_at: str
    source: str
    size_bytes: int
    rows: int
    accepted: int
    notes: list[str] = []
    history_companies: int = 0
    history_operations: int = 0
    rejected: int
    errors: list[DebugRowError]
    inserted: int
    duplicates: int
    companies: int
    first_date: date
    last_date: date
    quality: DebugQuality
    categories: DebugCategories


class DebugDataset(DebugDatasetSummary):
    accounts: list[DebugAccount]


class DebugDeleted(View):
    id: str
    removed_accounts: int


class DebugForecast(View):
    run_id: str
    first_gap_date: date | None
    liquidity_need_minor: int
    minimum_balance_minor: int
    probability_30d: float | None


class DebugBacktest(View):
    as_of: date
    days: int
    mae_minor: int
    mae_constant_minor: int
    skill: float | None
    forecast_minimum_minor: int
    actual_minimum_minor: int
    forecast_gap_date: date | None
    actual_gap_date: date | None


class DebugCheck(View):
    account_id: str
    as_of: date
    model: Literal["constant", "mean", "weekday", "hgb", "structural", "ensemble"]
    forecast: DebugForecast
    backtest: DebugBacktest | None
    backtest_note: str | None


# ---------------------------------------------------------------- AI (LLM)


class AIStatus(View):
    enabled: bool
    model: str | None
    calls_today: int
    tokens_today: int
    daily_token_limit: int
    spent_today_usd: float
    daily_budget_usd: float


class AICheck(View):
    title: str
    status: Literal["ok", "warning", "problem"]
    detail: str


class AIRecommendation(View):
    title: str
    detail: str
    priority: Literal["high", "medium", "low"]
    deadline: date | None
    amount_minor: int | None


class AIImprovement(View):
    title: str
    detail: str


class AIReview(View):
    run_id: str
    account_id: str
    model: str
    created_at: str
    verdict: Literal["ok", "caution", "weak"]
    headline: str
    trust: str
    checks: list[AICheck]
    recommendations: list[AIRecommendation]
    improvements: list[AIImprovement]
    backtest: dict
    reasoning: str
    elapsed: float
    cached: bool


class AIModelImprovement(View):
    title: str
    detail: str
    expected_effect: str
    how_to_validate: str
    priority: Literal["high", "medium", "low"]


class AIModelReview(View):
    dataset_id: str
    model: str
    summary: dict
    verdict: Literal["ok", "caution", "weak"]
    headline: str
    strengths: list[str]
    weaknesses: list[str]
    patterns: list[str]
    improvements: list[AIModelImprovement]
    risks: list[str]
    reasoning: str
    elapsed: float


class AIParty(View):
    name: str | None
    inn: str | None


class AIScheduleLine(View):
    date: date | None
    amount_minor: int | None
    label: str | None


class AIDocumentFields(View):
    doc_type: Literal["invoice", "contract", "act", "upd", "payment_order", "other"]
    number: str | None
    doc_date: date | None
    seller: AIParty
    buyer: AIParty
    direction: Literal["in", "out"] | None
    amount_minor: int | None
    vat_minor: int | None
    due_date: date | None
    subject: str | None
    schedule: list[AIScheduleLine]
    confidence: float
    notes: str | None


class AIDraftPayment(View):
    direction: Literal["in", "out"] | None
    amount_minor: int
    date: date | None
    label: str
    counterparty_name: str | None
    counterparty_inn: str | None
    source: Literal["invoice", "contract", "manual"]
    certainty: Literal["promised", "agreed", "documented"]
    category: str


class AIOcrResult(View):
    engine: Literal["tesseract + llm", "llm vision"]
    text: str
    fields: AIDocumentFields
    drafts: list[AIDraftPayment]
    elapsed: float


class AIChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class AIChatContext(BaseModel):
    account_id: str | None = None
    as_of: date | None = None
    path: str | None = Field(default=None, max_length=200)
    model: str | None = None
    run_id: str | None = None


class AIChatRequest(BaseModel):
    messages: list[AIChatMessage] = Field(min_length=1, max_length=40)
    context: AIChatContext = Field(default_factory=AIChatContext)


class AIAction(View):
    tool: str
    title: str
    ok: bool
    error: str | None = None


class AIChatReply(View):
    reply: str
    actions: list[AIAction]
    ui: list[dict]
    account_id: str | None
    as_of: date | None
    reasoning: str
    elapsed: float


class AIChatJob(View):
    job_id: str


class AIChatProgress(View):
    job_id: str
    status: Literal["running", "done", "error"]
    elapsed: float
    stage: str
    actions: list[AIAction]
    reasoning: list[str]
    result: AIChatReply | None
    error: str | None


# ---------------------------------------------------------------- webhooks


class WebhookDeliverySummary(View):
    received_at: str
    status: Literal["ok", "checked", "error"]
    summary: str
    error: str | None


class WebhookView(View):
    id: str
    account_id: str
    kind: Literal["statement", "1c", "bank", "marketplace", "ofd", "invoices", "contract"]
    kind_label: str
    label: str
    created_at: str
    active: bool
    path: str
    deliveries: int
    last_delivery: WebhookDeliverySummary | None


class WebhookCreated(WebhookView):
    secret: str


class WebhookSecret(View):
    id: str
    secret: str


class WebhookDeleted(View):
    id: str
    deleted: bool


class WebhookDelivery(View):
    id: int
    webhook_id: str
    kind: str
    received_at: str
    delivery_id: str | None
    status: Literal["ok", "checked", "error"]
    test: bool
    summary: str
    error: str | None


class WebhookResult(View):
    status: Literal["ok", "valid", "duplicate"]
    summary: str
    delivery: int | None = None
    delivery_id: str | None


class WebhookLastDelivery(View):
    received_at: str
    status: Literal["ok", "checked", "error"]
    summary: str
    error: str | None


class WebhookCheck(View):
    id: str
    status: Literal["waiting", "working", "failing", "disabled"]
    summary: str
    deliveries: int
    last_delivery: WebhookLastDelivery | None


class WebhookKind(View):
    kind: str
    label: str
    content_type: str
    example: str


class WebhookCreate(BaseModel):
    kind: Literal["statement", "1c", "bank", "marketplace", "ofd", "invoices", "contract"]
    label: str | None = Field(default=None, max_length=120)


# ---------------------------------------------------------------- dataset evaluation


class EvaluationState(View):
    dataset_id: str
    status: Literal["none", "running", "done", "error"]
    stage: str
    done: int
    total: int
    elapsed: float
    log: list[str]
    result: dict | None
    error: str | None


class EvaluationCompany(View):
    account_id: str
    current: dict
    history: list[dict]
    forecast: list[dict]
    windows: list[dict]
    trace: list[dict]


class TraceStep(View):
    n: int
    title: str
    detail: str
    items: list[str]
