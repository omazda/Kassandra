import json
import os
import secrets
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi import Path as PathParam
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from cashflow_domain.feeds import (
    AssumptionEdit,
    BankFeed,
    ContractSpec,
    EventEdit,
    EventText,
    InvoiceSpec,
    MarketplaceFeed,
    OfdFeed,
    TaxApply,
)
from cashflow_domain.money import to_minor
from cashflow_domain.schemas import (
    Category,
    EventCreate,
    ForecastRequest,
    ForecastResult,
    GapExplanation,
    PaymentLink,
    PlannedEvent,
    ScenarioResult,
    ScenarioSpec,
    Schema,
)
from cashflow_domain.views import (
    Account,
    AccountQuality,
    AIChatJob,
    AIChatProgress,
    AIChatReply,
    AIChatRequest,
    AIModelReview,
    AIOcrResult,
    AIReview,
    AIStatus,
    AlertsView,
    CalendarView,
    CategorySummary,
    CategoryUpdated,
    CounterpartyCheck,
    CounterpartyRisks,
    CoverageGapResult,
    DailyRow,
    DebugCheck,
    DebugDataset,
    DebugDatasetSummary,
    DebugDeleted,
    Dictionaries,
    EvaluationCompany,
    EvaluationState,
    ForecastSummary,
    Health,
    ImportReport,
    ParseResult,
    PaymentLinkResult,
    PlanFactView,
    ReconcileResult,
    RecurringView,
    RefreshResult,
    RegisterResult,
    SourcesView,
    TaxCalendarView,
    TopClient,
    TraceStep,
    TransactionsPage,
    WebhookCheck,
    WebhookCreate,
    WebhookCreated,
    WebhookDeleted,
    WebhookDelivery,
    WebhookKind,
    WebhookResult,
    WebhookSecret,
    WebhookView,
)
from cashflow_services.ai import Assistant, ChatJobs
from cashflow_services.evaluation_job import forecast_trace
from cashflow_services.integrations import parse_event_text
from cashflow_services.llm import LLMUnavailable
from cashflow_services.mcp_server import MCPServer
from cashflow_services.service import ConflictError, Service
from cashflow_services.webhooks import KINDS as WEBHOOK_KINDS
from cashflow_services.webhooks import sample_payload


class CategoryEdit(Schema):
    category: Category
    known_at: date | None = None


class CoverageGap(Schema):
    start_date: date
    end_date: date
    known_at: date


def create_app(service=None, users=None, auth_mode=None, assistant=None):
    load_dotenv()
    svc = service or Service(
        os.getenv("CASHFLOW_DB", "data/cashflow.sqlite"),
        os.getenv("CASHFLOW_ARTIFACTS", "artifacts"),
    )
    mode = auth_mode or os.getenv("CASHFLOW_AUTH_MODE", "demo")
    user_map = users if users is not None else json.loads(os.getenv("CASHFLOW_USERS_JSON") or "{}")
    if mode not in {"demo", "token"}:
        raise ValueError("Неизвестный CASHFLOW_AUTH_MODE")
    if mode == "token" and not user_map:
        raise ValueError("Для token-режима нужен CASHFLOW_USERS_JSON")
    app = FastAPI(title="Kassandra API", version="1.1.0")
    app.state.service = svc
    ai = assistant or Assistant(svc)
    jobs = ChatJobs(ai)
    mcp = MCPServer(svc)

    def principal(authorization: Annotated[str | None, Header()] = None):
        if authorization:
            token = (
                authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
            )
            for candidate, identity in user_map.items():
                if secrets.compare_digest(token, candidate):
                    return {"id": identity["id"], "accounts": identity.get("accounts", [])}
            raise PermissionError("Недействительные учётные данные")
        if mode == "demo":
            return {"id": "demo", "accounts": []}
        raise PermissionError("Требуется Bearer-авторизация")

    User = Annotated[dict, Depends(principal)]

    @app.exception_handler(PermissionError)
    async def permission_error(_, exc):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(LLMUnavailable)
    async def llm_unavailable(_, exc):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def conflict(_, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def invalid(_, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)[:400]})

    @app.exception_handler(RequestValidationError)
    async def validation(_, exc):
        # Never echo the entire uploaded financial context in validation errors.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                    for e in exc.errors()
                ]
            },
        )

    @app.get("/health", response_model=Health)
    def health():
        with svc.store.connect() as db:
            db.execute("SELECT 1")
        return {"status": "ok", "llm_enabled": os.getenv("LLM_ENABLED", "false").lower() == "true"}

    @app.get("/api/v1/accounts", response_model=list[Account])
    def accounts(user: User):
        return svc.accounts(user)

    @app.post("/api/v1/imports/transactions", response_model=ImportReport)
    async def import_transactions(user: User, file: Annotated[UploadFile, File()]):
        payload = await file.read(80 * 1024 * 1024 + 1)
        if len(payload) > 80 * 1024 * 1024:
            raise ValueError("Размер CSV превышает 80 МБ")
        return svc.import_transactions(payload, user)

    @app.get("/api/v1/accounts/{account_id}/transactions", response_model=TransactionsPage)
    def transactions(
        account_id: str,
        user: User,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        date_from: date | None = None,
        date_to: date | None = None,
        direction: Literal["in", "out"] | None = None,
        category: Annotated[list[str] | None, Query()] = None,
        group: Annotated[list[str] | None, Query()] = None,
        counterparty_inn: str | None = None,
        q: str | None = Query(None, max_length=100),
        amount_min: int | None = Query(None, ge=0),
        amount_max: int | None = Query(None, ge=0),
        sort: Literal["date_desc", "date_asc", "amount_desc", "amount_asc"] = "date_desc",
    ):
        """Statement operations. Filters combine with AND; `date_to` = the forecast date hides
        operations a historical slice could not know. Totals and facets cover the filtered set."""
        filters = {
            "date_from": date_from,
            "date_to": date_to,
            "direction": direction,
            "category": category,
            "group": group,
            "counterparty_inn": counterparty_inn,
            "q": q,
            "amount_min": amount_min,
            "amount_max": amount_max,
            "sort": sort,
        }
        return svc.transactions_view(account_id, user, filters, page, page_size)

    @app.get("/api/v1/accounts/{account_id}/daily", response_model=list[DailyRow])
    def daily(account_id: str, user: User):
        svc.authorize(user, account_id)
        return svc.store.daily(account_id)

    @app.get("/api/v1/accounts/{account_id}/quality", response_model=AccountQuality)
    def quality(account_id: str, user: User):
        svc.authorize(user, account_id)
        return svc.store.quality(account_id)

    @app.patch(
        "/api/v1/transactions/{account_id}/{operation_id}/category", response_model=CategoryUpdated
    )
    def category(account_id: str, operation_id: str, body: CategoryEdit, user: User):
        svc.authorize(user, account_id)
        with svc.lock, svc.store.connect() as db:
            operation = db.execute(
                "SELECT operation_date FROM transactions WHERE account_id=? AND operation_id=?",
                (account_id, operation_id),
            ).fetchone()
            if not operation:
                raise ValueError("Операция не найдена")
            known_at = body.known_at or date.today()
            if str(known_at) < operation[0]:
                raise ValueError("Дата правки раньше фактической операции")
            db.execute(
                "INSERT INTO category_revisions(account_id,operation_id,known_at,category) VALUES(?,?,?,?)",
                (account_id, operation_id, str(known_at), body.category.value),
            )
            db.execute("UPDATE accounts SET data_version=data_version+1 WHERE id=?", (account_id,))
        return {"status": "updated", "category": body.category}

    @app.post("/api/v1/accounts/{account_id}/coverage-gaps", response_model=CoverageGapResult)
    def coverage_gap(account_id: str, body: CoverageGap, user: User):
        return svc.mark_missing(account_id, body.start_date, body.end_date, body.known_at, user)

    @app.post("/api/v1/forecasts", response_model=ForecastResult)
    def forecast(
        body: ForecastRequest, user: User, idempotency_key: Annotated[str | None, Header()] = None
    ):
        svc.authorize(user, body.account_id)
        return svc.idempotent(
            user,
            "forecast",
            idempotency_key,
            body.model_dump(mode="json"),
            lambda: svc.forecast(body),
        )

    @app.get("/api/v1/accounts/{account_id}/forecasts", response_model=list[ForecastSummary])
    def forecasts(account_id: str, user: User):
        svc.authorize(user, account_id)
        with svc.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM forecasts WHERE account_id=? ORDER BY rowid DESC LIMIT 100",
                (account_id,),
            ).fetchall()
        return [
            {
                k: json.loads(r[0])[k]
                for k in ("run_id", "as_of", "model_version", "data_version", "event_version")
            }
            for r in rows
        ]

    @app.get("/api/v1/forecasts/{run_id}", response_model=ForecastResult)
    def get_forecast(run_id: str, user: User):
        result = svc.get_forecast(run_id)
        svc.authorize(user, result.account_id)
        return result

    @app.get("/api/v1/accounts/{account_id}/events", response_model=list[PlannedEvent])
    def events(account_id: str, user: User, as_of: date | None = None):
        svc.authorize(user, account_id)
        # Without a cut, return the full register. Forecasts always pass their own as_of.
        return svc.events(account_id, as_of or date.max)

    @app.post("/api/v1/events", response_model=PlannedEvent)
    def add_event(body: EventCreate, user: User):
        return svc.add_event(body, user)

    @app.post("/api/v1/imports/events", response_model=list[PlannedEvent])
    async def import_events(
        user: User, file: Annotated[UploadFile, File()], base_run_id: str | None = None
    ):
        payload = await file.read(2 * 1024 * 1024 + 1)
        if len(payload) > 2 * 1024 * 1024:
            raise ValueError("Слишком большой реестр")
        return svc.import_events(payload, user, base_run_id)

    @app.patch("/api/v1/events/{event_id}", response_model=PlannedEvent)
    def edit_event(event_id: str, body: EventEdit, user: User):
        """New revision of a registered event: date, amount, category, label, counterparty or
        certainty. Fields left out keep their value; `{expected_date, known_at}` reschedules."""
        return svc.edit_event(event_id, body, user)

    @app.delete("/api/v1/events/{event_id}", response_model=PlannedEvent)
    def cancel_event(event_id: str, user: User, known_at: date):
        """Cancels a registered event from `known_at` on: forecasts for earlier dates keep it."""
        return svc.cancel_event(event_id, known_at, user)

    @app.post("/api/v1/forecasts/{run_id}/assumptions/{assumption_id}", response_model=PlannedEvent)
    def edit_assumption(run_id: str, assumption_id: str, body: AssumptionEdit, user: User):
        """Corrects a history-based expectation of this forecast (regular payment, tax,
        document): `edit` registers the user's date or amount instead, `skip` removes it."""
        return svc.edit_assumption(run_id, assumption_id, body, user)

    @app.get("/api/v1/forecasts/{run_id}/calendar", response_model=CalendarView)
    def calendar(
        run_id: str,
        user: User,
        date_from: date | None = None,
        date_to: date | None = None,
        direction: Annotated[list[Literal["in", "out"]] | None, Query()] = None,
        kind: Annotated[list[Literal["assumption", "registered"]] | None, Query()] = None,
        source: Annotated[list[str] | None, Query()] = None,
        category: Annotated[list[str] | None, Query()] = None,
        group: Annotated[list[str] | None, Query()] = None,
        obligatory: bool = False,
        include_cancelled: bool = False,
        q: str | None = Query(None, max_length=100),
        amount_min: int | None = Query(None, ge=0),
        amount_max: int | None = Query(None, ge=0),
    ):
        """Payment calendar of a forecast: every expected payment with `actions` the user may
        take (`edit`, `delete` for registered events, `edit`, `skip` for expectations). Facets
        count the whole calendar, totals the filtered items."""
        filters = {
            "date_from": date_from,
            "date_to": date_to,
            "direction": direction,
            "kind": kind,
            "source": source,
            "category": category,
            "group": group,
            "obligatory": obligatory,
            "include_cancelled": include_cancelled,
            "q": q,
            "amount_min": amount_min,
            "amount_max": amount_max,
        }
        return svc.calendar_view(run_id, user, filters)

    @app.post("/api/v1/events/{event_id}/payments", response_model=PaymentLinkResult)
    def payment(event_id: str, body: PaymentLink, user: User):
        return svc.link_payment(event_id, body, user)

    @app.post("/api/v1/scenarios/evaluate", response_model=ScenarioResult)
    def evaluate_scenario(
        body: ScenarioSpec, user: User, idempotency_key: Annotated[str | None, Header()] = None
    ):
        base = svc.get_forecast(body.base_run_id)
        svc.authorize(user, base.account_id)
        return svc.idempotent(
            user,
            "scenario",
            idempotency_key,
            body.model_dump(mode="json"),
            lambda: svc.evaluate_scenario(body),
        )

    @app.get("/api/v1/plan-fact", response_model=PlanFactView)
    def plan_fact(run_id: str, user: User, granularity: Literal["day", "week"] = "day"):
        base = svc.get_forecast(run_id)
        svc.authorize(user, base.account_id)
        return svc.plan_fact_view(run_id, granularity)

    @app.get("/api/v1/scenarios/{scenario_id}", response_model=ScenarioResult)
    def get_scenario(scenario_id: str, user: User):
        result = svc.get_scenario(scenario_id)
        svc.authorize(user, svc.get_forecast(result.base_run_id).account_id)
        return result

    @app.get("/api/v1/accounts/{account_id}/top-clients", response_model=list[TopClient])
    def top_clients(
        account_id: str,
        user: User,
        as_of: date,
        limit: int = Query(10, ge=1, le=50),
        run_id: str | None = None,
    ):
        """Top clients by operating-revenue receipts in the 90 days up to as_of."""
        svc.authorize(user, account_id)
        return svc.top_clients(account_id, as_of, limit, run_id=run_id)

    @app.get("/api/v1/forecasts/{run_id}/explanation", response_model=GapExplanation)
    def forecast_explanation(run_id: str, user: User):
        svc.authorize(user, svc.get_forecast(run_id).account_id)
        return svc.explain(run_id=run_id)

    @app.get("/api/v1/scenarios/{scenario_id}/explanation", response_model=GapExplanation)
    def scenario_explanation(scenario_id: str, user: User):
        result = svc.get_scenario(scenario_id)
        svc.authorize(user, svc.get_forecast(result.base_run_id).account_id)
        return svc.explain(scenario_id=scenario_id)

    # ---------------------------------------------------------------- v1.1: sources beyond the statement

    @app.get("/api/v1/dictionaries", response_model=Dictionaries)
    def dictionaries(user: User):
        """Categories, event sources and certainty levels with Russian labels."""
        return svc.dictionaries()

    @app.get("/api/v1/accounts/{account_id}/categories/summary", response_model=CategorySummary)
    def categories_summary(
        account_id: str,
        user: User,
        date_from: date | None = None,
        date_to: date | None = None,
        direction: Literal["in", "out"] | None = None,
        group: Annotated[list[str] | None, Query()] = None,
        counterparty_inn: str | None = None,
        q: str | None = Query(None, max_length=100),
        amount_min: int | None = Query(None, ge=0),
        amount_max: int | None = Query(None, ge=0),
    ):
        """Shares by category and by group (`groups`: slices of the structure chart). Accepts the
        operations list filters, so the chart describes exactly the filtered operations."""
        svc.authorize(user, account_id)
        filters = {
            "direction": direction,
            "group": group,
            "counterparty_inn": counterparty_inn,
            "q": q,
            "amount_min": amount_min,
            "amount_max": amount_max,
        }
        return svc.categories_summary(account_id, date_from, date_to, filters)

    @app.get("/api/v1/accounts/{account_id}/recurring", response_model=RecurringView)
    def recurring(account_id: str, user: User, as_of: date):
        """Repeating receipts and payments detected in the history up to as_of."""
        svc.authorize(user, account_id)
        return svc.recurring(account_id, as_of)

    @app.get("/api/v1/accounts/{account_id}/tax-calendar", response_model=TaxCalendarView)
    def tax_calendar(
        account_id: str,
        user: User,
        as_of: date,
        horizon_days: int = Query(90, ge=30, le=366),
        run_id: str | None = None,
    ):
        svc.authorize(user, account_id)
        return svc.tax_calendar(account_id, as_of, horizon_days, run_id)

    @app.post("/api/v1/accounts/{account_id}/tax-calendar/apply", response_model=RegisterResult)
    def apply_tax_calendar(account_id: str, body: TaxApply, user: User):
        return svc.apply_tax_calendar(account_id, body.base_run_id, user)

    @app.post("/api/v1/accounts/{account_id}/contracts", response_model=RegisterResult)
    def add_contract(account_id: str, body: ContractSpec, user: User):
        return svc.add_contract(account_id, body, user)

    @app.post("/api/v1/accounts/{account_id}/invoices", response_model=RegisterResult)
    def add_invoice(account_id: str, body: InvoiceSpec, user: User):
        return svc.add_invoices(account_id, [body], user)

    @app.post("/api/v1/accounts/{account_id}/imports/invoices", response_model=RegisterResult)
    async def import_invoices(
        account_id: str,
        user: User,
        file: Annotated[UploadFile, File()],
        base_run_id: str | None = None,
        known_at: date | None = None,
    ):
        payload = await file.read(2 * 1024 * 1024 + 1)
        if len(payload) > 2 * 1024 * 1024:
            raise ValueError("Слишком большой реестр счетов")
        return svc.import_invoices(account_id, payload, user, base_run_id, known_at)

    @app.post("/api/v1/accounts/{account_id}/imports/1c", response_model=ImportReport)
    async def import_1c(
        account_id: str,
        user: User,
        file: Annotated[UploadFile, File()],
        opening_balance: str | None = None,
    ):
        """Statement exported from 1С («Обмен с банком», 1CClientBankExchange)."""
        payload = await file.read(40 * 1024 * 1024 + 1)
        if len(payload) > 40 * 1024 * 1024:
            raise ValueError("Размер выгрузки превышает 40 МБ")
        opening = to_minor(opening_balance.replace(",", ".")) if opening_balance else None
        return svc.import_1c(account_id, payload, user, opening)

    @app.post("/api/v1/accounts/{account_id}/integrations/bank", response_model=RegisterResult)
    def bank_feed(account_id: str, body: BankFeed, user: User):
        """Loan schedules, payroll project and created but not executed payment orders."""
        return svc.add_feed(account_id, body, user, "bank")

    @app.post(
        "/api/v1/accounts/{account_id}/integrations/marketplace", response_model=RegisterResult
    )
    def marketplace_feed(account_id: str, body: MarketplaceFeed, user: User):
        return svc.add_feed(account_id, body, user, "marketplace")

    @app.post("/api/v1/accounts/{account_id}/integrations/ofd", response_model=RegisterResult)
    def ofd_feed(account_id: str, body: OfdFeed, user: User):
        return svc.add_feed(account_id, body, user, "ofd")

    @app.post("/api/v1/events/parse", response_model=ParseResult)
    def parse_event(body: EventText, user: User):
        """Draft of a planned event from a phrase; nothing is saved."""
        if body.account_id:
            svc.authorize(user, body.account_id)
        return parse_event_text(body.text, body.today)

    @app.get("/api/v1/counterparties/{inn}/check", response_model=CounterpartyCheck)
    def counterparty_check(
        inn: Annotated[str, PathParam(pattern=r"^(\d{10}|\d{12})$")], user: User
    ):
        return svc.counterparty_check(inn, user)

    @app.get("/api/v1/accounts/{account_id}/counterparty-risks", response_model=CounterpartyRisks)
    def counterparty_risks(
        account_id: str, user: User, as_of: date, limit: int = Query(10, ge=1, le=30)
    ):
        svc.authorize(user, account_id)
        return svc.counterparty_risks(account_id, as_of, user, limit)

    @app.post("/api/v1/accounts/{account_id}/reconcile", response_model=ReconcileResult)
    def reconcile(account_id: str, user: User, dry_run: bool = False):
        """Link registered events to the statement operations that paid them."""
        return svc.reconcile(account_id, user, dry_run)

    @app.post("/api/v1/forecasts/{run_id}/refresh", response_model=RefreshResult)
    def refresh_forecast(run_id: str, user: User):
        """Re-check the forecast after new data without retraining or, for events, a model run."""
        return svc.refresh(run_id, user)

    @app.get("/api/v1/accounts/{account_id}/alerts", response_model=AlertsView)
    def alerts(account_id: str, user: User, run_id: str | None = None):
        return svc.alerts(account_id, user, run_id)

    @app.get("/api/v1/accounts/{account_id}/sources", response_model=SourcesView)
    def sources(account_id: str, user: User):
        return svc.sources_summary(account_id, user)

    @app.get("/api/v1/experiments")
    def experiments(user: User):
        # Contains aggregates/config only, never raw operations or account credentials.
        results = svc.artifacts / "evaluation" / "metrics.json"
        if not results.exists():
            results = Path("experiments/results/metrics.json")
        return {
            "metrics": json.loads(results.read_text()) if results.exists() else None,
            "registry": json.loads(Path("experiments/registry.json").read_text())
            if Path("experiments/registry.json").exists()
            else [],
        }

    # LLM (Xiaomi MiMo): review and recommendations, model review, OCR, chat agent
    # (src/cashflow_services/ai.py, instructions in prompts/). Numbers come from the service.
    @app.get("/api/v1/ai/status", response_model=AIStatus)
    def ai_status(user: User):
        return ai.client.status()

    @app.get("/api/v1/ai/forecasts/{run_id}/review", response_model=AIReview | None)
    def ai_cached_review(run_id: str, user: User):
        # Free: only a review already paid for; null tells the client to offer the button.
        return ai.cached_review(run_id, user)

    @app.post("/api/v1/ai/forecasts/{run_id}/review", response_model=AIReview)
    def ai_review(run_id: str, user: User, refresh: bool = False):
        return ai.review(run_id, user, refresh=refresh)

    @app.post("/api/v1/ai/debug/{dataset_id}/review", response_model=AIModelReview)
    def ai_model_review(dataset_id: str, user: User):
        return ai.model_review(dataset_id)

    @app.post("/api/v1/ai/ocr", response_model=AIOcrResult)
    def ai_ocr(
        user: User,
        file: Annotated[UploadFile, File()],
        account_id: Annotated[str | None, Form()] = None,
    ):
        payload = file.file.read(15 * 1024 * 1024 + 1)
        return ai.ocr(file.filename or "document", payload, user, account_id)

    @app.post("/api/v1/ai/chat/jobs", response_model=AIChatJob)
    def ai_chat_start(body: AIChatRequest, user: User):
        # The answer is computed in the background; the client polls the stage and timer.
        messages = [m.model_dump() for m in body.messages]
        return {"job_id": jobs.start(messages, body.context.model_dump(mode="json"), user)}

    @app.get("/api/v1/ai/chat/jobs/{job_id}", response_model=AIChatProgress)
    def ai_chat_progress(job_id: str, user: User):
        return jobs.get(job_id, user)

    @app.post("/api/v1/ai/chat", response_model=AIChatReply)
    def ai_chat(body: AIChatRequest, user: User):
        return ai.chat(
            [m.model_dump() for m in body.messages], body.context.model_dump(mode="json"), user
        )

    # Webhooks: outside systems push data into an account automatically (webhooks.py).
    @app.get("/api/v1/webhooks/kinds", response_model=list[WebhookKind])
    def webhook_kinds(user: User):
        result = []
        for kind, label in WEBHOOK_KINDS.items():
            body, content_type = sample_payload(kind, "C000", date(2026, 5, 11))
            example = body.decode("cp1251" if kind == "1c" else "utf-8", errors="replace")
            result.append(
                {
                    "kind": kind,
                    "label": label,
                    "content_type": content_type,
                    "example": example[:3000],
                }
            )
        return result

    @app.get("/api/v1/accounts/{account_id}/webhooks", response_model=list[WebhookView])
    def webhooks(account_id: str, user: User):
        return svc.webhooks(account_id, user)

    @app.post("/api/v1/accounts/{account_id}/webhooks", response_model=WebhookCreated)
    def create_webhook(account_id: str, body: WebhookCreate, user: User):
        return svc.create_webhook(account_id, body.kind, body.label, user)

    @app.get(
        "/api/v1/accounts/{account_id}/webhooks/deliveries",
        response_model=list[WebhookDelivery],
    )
    def webhook_deliveries(account_id: str, user: User, limit: int = Query(30, ge=1, le=100)):
        return svc.webhook_deliveries(account_id, user, limit)

    @app.post("/api/v1/webhooks/{hook_id}/rotate", response_model=WebhookSecret)
    def rotate_webhook(hook_id: str, user: User):
        return svc.rotate_webhook(hook_id, user)

    @app.delete("/api/v1/webhooks/{hook_id}", response_model=WebhookDeleted)
    def delete_webhook(hook_id: str, user: User):
        return svc.delete_webhook(hook_id, user)

    @app.post("/api/v1/webhooks/{hook_id}/check", response_model=WebhookCheck)
    def check_webhook(hook_id: str, user: User):
        return svc.check_webhook(hook_id, user)

    @app.post("/api/v1/webhooks/{hook_id}/validate", response_model=WebhookResult)
    async def validate_webhook(hook_id: str, request: Request, user: User):
        # The body the user's system would send: checked, never written or journaled.
        body = await request.body()
        content_type = request.headers.get("content-type", "")
        return await run_in_threadpool(svc.validate_webhook, hook_id, user, body, content_type)

    # Receiving end for outside systems: no user session, the HMAC signature or the webhook
    # secret proves the sender.
    @app.post("/api/v1/hooks/{hook_id}", response_model=WebhookResult)
    async def receive_webhook(hook_id: str, request: Request):
        body = await request.body()
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            return await run_in_threadpool(
                svc.receive_webhook, hook_id, body, headers, headers.get("content-type", "")
            )
        except PermissionError as exc:
            return JSONResponse(status_code=401, content={"detail": str(exc)})

    # How one forecast was built, step by step (for the checkpoint: «пошаговые логи»).
    @app.get("/api/v1/forecasts/{run_id}/trace", response_model=list[TraceStep])
    def forecast_trace_view(run_id: str, user: User):
        forecast = svc.get_forecast(run_id)
        svc.authorize(user, forecast.account_id)
        return forecast_trace(svc, forecast)

    # MCP connectors (Model Context Protocol, Streamable HTTP, JSON responses): read-only «Get»
    # tools of the system for the chat agent and for outside MCP clients. Same auth as the API.
    @app.post("/api/v1/mcp")
    async def mcp_endpoint(request: Request, user: User):
        try:
            message = await request.json()
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                },
            )
        answer = await run_in_threadpool(mcp.handle, message, user)
        if answer is None:
            return Response(status_code=202)
        return JSONResponse(answer)

    @app.get("/api/v1/mcp")
    def mcp_stream():
        # No server-initiated stream: every answer comes in the POST response.
        return Response(status_code=405, headers={"Allow": "POST"})

    # Debug page: the jury uploads a hackathon-format dataset and checks it end to end.
    # Off unless CASHFLOW_DEBUG_DATASETS=true; the sets live under their own dbgN- ids.
    if os.getenv("CASHFLOW_DEBUG_DATASETS", "false").lower() == "true":

        @app.get("/api/v1/debug/datasets", response_model=list[DebugDatasetSummary])
        def debug_datasets(user: User):
            return svc.debug_datasets()

        @app.post("/api/v1/debug/datasets", response_model=DebugDataset)
        def debug_upload(user: User, file: Annotated[UploadFile, File()], history: bool = True):
            payload = file.file.read(MAX_DEBUG_UPLOAD + 1)
            if len(payload) > MAX_DEBUG_UPLOAD:
                raise ValueError("Файл больше 100 МБ: загрузите ZIP")
            return svc.debug_upload(file.filename or "dataset", payload, history)

        @app.get("/api/v1/debug/datasets/{dataset_id}", response_model=DebugDataset)
        def debug_dataset(dataset_id: str, user: User):
            return svc.debug_dataset(dataset_id)

        @app.delete("/api/v1/debug/datasets/{dataset_id}", response_model=DebugDeleted)
        def debug_delete(dataset_id: str, user: User):
            return svc.debug_delete(dataset_id)

        @app.post("/api/v1/debug/datasets/{dataset_id}/evaluate", response_model=EvaluationState)
        def debug_evaluate(dataset_id: str, user: User):
            return svc.start_evaluation(dataset_id, user)

        @app.get("/api/v1/debug/datasets/{dataset_id}/evaluation", response_model=EvaluationState)
        def debug_evaluation(dataset_id: str, user: User):
            return svc.evaluation(dataset_id)

        @app.get(
            "/api/v1/debug/datasets/{dataset_id}/evaluation/companies/{account_id}",
            response_model=EvaluationCompany,
        )
        def debug_evaluation_company(dataset_id: str, account_id: str, user: User):
            return svc.evaluation_company(dataset_id, account_id)

        @app.get("/api/v1/debug/datasets/{dataset_id}/evaluation/report.md")
        def debug_evaluation_report(dataset_id: str, user: User):
            return Response(
                content=svc.evaluation_report(dataset_id),
                media_type="text/markdown; charset=utf-8",
                headers={
                    "Content-Disposition": f'attachment; filename="kassandra-{dataset_id}-report.md"'
                },
            )

        @app.get("/api/v1/debug/accounts/{account_id}/check", response_model=DebugCheck)
        def debug_check(account_id: str, user: User):
            return svc.debug_check(account_id)

    return app


MAX_DEBUG_UPLOAD = 100 * 1024 * 1024
app = create_app()
