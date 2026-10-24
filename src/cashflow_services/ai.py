"""The LLM on top of the forecast: review and recommendations, model review, OCR, chat agent.

Numbers always come from the service (forecast, calendar, scenarios); the model reads them through
compact JSON contexts or tools and writes the words. Instructions live in prompts/*.md;
the provider (now Xiaomi MiMo) is configuration in llm.py.
"""

import base64
import csv
import io
import json
import os
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from functools import cache
from pathlib import Path

from cashflow_domain.schemas import (
    CERTAINTY_LABELS,
    SOURCE_LABELS,
    EventCreate,
    ForecastRequest,
    PlannedEvent,
    ScenarioSpec,
)
from cashflow_services.llm import LLMClient, LLMUnavailable
from cashflow_services.mcp_server import (
    MODEL_CARD,
    MCPServer,
    Reader,
    ToolError,
    openai_tools,
    rub,
)
from cashflow_services.mcp_server import TOOLS as MCP_TOOLS

PROMPTS = Path(__file__).parent / "prompts"
MAX_OCR_BYTES = 15 * 1024 * 1024
MAX_TOOL_STEPS = 8


@cache
def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


@cache
def company_inns() -> dict:
    path = os.getenv("CASHFLOW_COMPANIES", "dataset/companies.csv")
    try:
        with open(path, encoding="utf-8-sig") as handle:
            return {
                r["company_id"]: r.get("company_inn", "")
                for r in csv.DictReader(handle, delimiter=";")
            }
    except OSError:
        return {}


def _pick(items, keys):
    return [{k: item.get(k) for k in keys} for item in items]


class Assistant:
    def __init__(self, service, client: LLMClient | None = None):
        self.svc = service
        self.client = client or LLMClient()

    # ------------------------------------------------------------------ forecast review

    def forecast_context(self, forecast) -> dict:
        svc = self.svc
        points = forecast.points
        closing = [p.closing_balance_minor for p in points]
        low = min(range(len(points)), key=lambda i: closing[i])
        explanation = svc.explain(run_id=forecast.run_id)
        quality = svc.store.quality(forecast.account_id)
        first = date.fromisoformat(quality["first_date"]) if quality.get("first_date") else None
        backtest, note = (None, "Нет истории")
        if first:
            backtest, note = svc.account_backtest(
                forecast.account_id, first, forecast.as_of, forecast.model
            )
        daily = [
            d
            for d in svc.store.daily(forecast.account_id)
            if forecast.as_of - timedelta(days=90) < date.fromisoformat(d["date"]) <= forecast.as_of
        ]
        events = sorted(
            (e for e in forecast.events if e.status != "cancelled"),
            key=lambda e: -e.remaining_minor,
        )[:14]
        return {
            "forecast": {
                "account_id": forecast.account_id,
                "as_of": str(forecast.as_of),
                "model": forecast.model,
                "opening_balance": rub(forecast.opening_balance_minor),
                "balances": {
                    str(points[i].date): rub(closing[i])
                    for i in sorted({0, 6, 13, 20, low, len(points) - 1})
                },
                "minimum": {"date": str(points[low].date), "balance": rub(closing[low])},
                "first_gap_date": str(forecast.risk.first_gap_date or "") or None,
                "liquidity_need": rub(forecast.risk.liquidity_need_minor),
                "liquidity_need_minor": forecast.risk.liquidity_need_minor,
                "probability_30d": forecast.risk.probability_30d,
                "probability_status": forecast.risk.calibration_status,
                "p10_p90_day30": [rub(points[-1].p10_minor), rub(points[-1].p90_minor)],
                "inflow_30d": rub(sum(p.event_in_minor + p.residual_in_minor for p in points)),
                "outflow_30d": rub(sum(p.event_out_minor + p.residual_out_minor for p in points)),
            },
            "events": [
                {
                    "date": str(e.expected_date),
                    "direction": e.direction,
                    "amount": rub(e.remaining_minor),
                    "label": e.label[:90],
                    "source": SOURCE_LABELS.get(e.source, e.source),
                    "certainty": CERTAINTY_LABELS.get(e.certainty or "", None),
                    "category": e.category,
                }
                for e in events
            ],
            "explanation": {
                "why": explanation.why,
                "actions": [a.text for a in explanation.actions],
            },
            "history": {
                "days": len(daily),
                "average_inflow_per_day": rub(
                    sum(d["inflow_minor"] for d in daily) // max(len(daily), 1)
                ),
                "average_outflow_per_day": rub(
                    sum(d["outflow_minor"] for d in daily) // max(len(daily), 1)
                ),
                "minimum_90d": rub(min((d["balance_minor"] for d in daily), default=None)),
            },
            "backtest": self._backtest_text(backtest) if backtest else {"note": note},
            "quality": {
                "flags": forecast.quality_flags,
                "confidence": forecast.confidence,
                "history_days": quality.get("calendar_days"),
                "balance_mismatches": len(quality.get("inconsistent_transitions", [])),
                "masked_days": quality.get("invalid_days"),
            },
            "model_card": MODEL_CARD,
        }

    @staticmethod
    def _backtest_text(test: dict) -> dict:
        return {
            "forecast_made_on": str(test["as_of"]),
            "days_checked": test["days"],
            "model_mae": rub(test["mae_minor"]),
            "constant_mae": rub(test["mae_constant_minor"]),
            "skill": test["skill"],
            "forecast_minimum": rub(test["forecast_minimum_minor"]),
            "actual_minimum": rub(test["actual_minimum_minor"]),
            "forecast_gap_date": str(test["forecast_gap_date"] or "") or None,
            "actual_gap_date": str(test["actual_gap_date"] or "") or None,
        }

    def _signature(self, forecast) -> str:
        # A new run with the same inputs is the same forecast: its review is reused for free.
        # The LLM model is part of it: another provider writes another review.
        return "|".join(
            str(v)
            for v in (
                self.client.settings.model_id,
                forecast.account_id,
                forecast.as_of,
                forecast.model,
                forecast.model_version,
                forecast.data_version,
                forecast.event_version,
                forecast.calendar,
                forecast.reserve_minor,
                forecast.credit_limit_minor,
            )
        )

    def cached_review(self, run_id: str, principal) -> dict | None:
        forecast = self.svc.get_forecast(run_id)
        self.svc.authorize(principal, forecast.account_id)
        signature = self._signature(forecast)
        with self.svc.store.connect() as db:
            rows = db.execute(
                "SELECT run_id, payload FROM ai_reviews WHERE account_id=? ORDER BY created_at DESC",
                (forecast.account_id,),
            ).fetchall()
        for row_run, raw in rows:
            payload = json.loads(raw)
            if row_run == run_id or payload.pop("signature", None) == signature:
                payload.pop("signature", None)
                return {**payload, "run_id": run_id, "cached": True}
        return None

    def review(self, run_id: str, principal, refresh: bool = False) -> dict:
        svc = self.svc
        forecast = svc.get_forecast(run_id)
        svc.authorize(principal, forecast.account_id)
        if not refresh and (cached := self.cached_review(run_id, principal)):
            return cached
        context = self.forecast_context(forecast)
        answer, reply = self.client.json(
            [
                {"role": "system", "content": prompt("reviewer")},
                {
                    "role": "user",
                    "content": "Данные расчёта:\n```json\n"
                    + json.dumps(context, ensure_ascii=False, default=str)
                    + "\n```",
                },
            ],
            max_tokens=6000,
        )
        payload = {
            "run_id": run_id,
            "account_id": forecast.account_id,
            "model": self.client.settings.model_id,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            **_clean_review(answer),
            "backtest": context["backtest"],
            "reasoning": reply["reasoning"][:2500],
            "elapsed": reply["elapsed"],
            "cached": False,
        }
        stored = {**payload, "signature": self._signature(forecast)}
        with svc.lock, svc.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO ai_reviews(run_id, account_id, created_at, payload) "
                "VALUES(?,?,?,?)",
                (
                    run_id,
                    forecast.account_id,
                    payload["created_at"],
                    json.dumps(stored, ensure_ascii=False),
                ),
            )
        return payload

    # ------------------------------------------------------------------ model review

    def model_review(self, dataset_id: str) -> dict:
        svc = self.svc
        report = svc.debug_dataset(dataset_id)
        state = svc.evaluation(dataset_id)
        if state["status"] == "done" and state["result"]:
            context = evaluation_context(report, state["result"])
            return self._model_review(dataset_id, context)
        checks = svc.debug_checks(dataset_id)
        tested = [c for c in checks if c.get("backtest")]
        if len(tested) < 5:
            raise ValueError("Сначала проверьте компании набора: нужно хотя бы 5 проверок")
        skills = sorted(
            c["backtest"]["skill"] for c in tested if c["backtest"]["skill"] is not None
        )
        by_skill = sorted(
            (c for c in tested if c["backtest"]["skill"] is not None),
            key=lambda c: c["backtest"]["skill"],
        )
        names = {a["account_id"]: a for a in report["accounts"]}

        def row(check):
            account = names.get(check["account_id"], {})
            test = check["backtest"]
            return {
                "company": account.get("company_id"),
                "operations": account.get("operations"),
                "history": f"{account.get('first_date')} — {account.get('last_date')}",
                "balance_mismatches": account.get("inconsistent_transitions"),
                "skill": test["skill"],
                "model_mae": rub(test["mae_minor"]),
                "constant_mae": rub(test["mae_constant_minor"]),
                "forecast_minimum": rub(test["forecast_minimum_minor"]),
                "actual_minimum": rub(test["actual_minimum_minor"]),
                "actual_gap": str(test["actual_gap_date"] or "") or None,
                "forecast_gap": str(test["forecast_gap_date"] or "") or None,
                "model": check.get("model"),
            }

        actual = [c for c in tested if c["backtest"]["actual_gap_date"]]
        context = {
            "dataset": {k: report[k] for k in ("companies", "rows", "accepted", "rejected")}
            | {"period": f"{report['first_date']} — {report['last_date']}"}
            | {
                "quality": report["quality"],
                "unknown_share": report["categories"]["unknown_share"],
            },
            "summary": {
                "checked": len(checks),
                "with_backtest": len(tested),
                "model_mae_mean": rub(
                    sum(c["backtest"]["mae_minor"] for c in tested) // len(tested)
                ),
                "constant_mae_mean": rub(
                    sum(c["backtest"]["mae_constant_minor"] for c in tested) // len(tested)
                ),
                "better_than_constant": sum(s > 0 for s in skills),
                "median_skill": skills[len(skills) // 2] if skills else None,
                "actual_gaps": len(actual),
                "gaps_caught": sum(bool(c["backtest"]["forecast_gap_date"]) for c in actual),
                "false_alarms": sum(
                    bool(c["backtest"]["forecast_gap_date"])
                    and not c["backtest"]["actual_gap_date"]
                    for c in tested
                ),
                "fallback_model": sum(c.get("model") != "hgb" for c in checks),
            },
            "worst": [row(c) for c in by_skill[:6]],
            "best": [row(c) for c in by_skill[-4:]],
            "model_card": MODEL_CARD,
        }
        return self._model_review(dataset_id, context)

    def _model_review(self, dataset_id: str, context: dict) -> dict:
        answer, reply = self.client.json(
            [
                {"role": "system", "content": prompt("model_review")},
                {
                    "role": "user",
                    "content": "Сводка проверки:\n```json\n"
                    + json.dumps(context, ensure_ascii=False, default=str)
                    + "\n```",
                },
            ],
            max_tokens=6000,
        )
        return {
            "dataset_id": dataset_id,
            "model": self.client.settings.model_id,
            "summary": context["summary"],
            "verdict": _choice(answer.get("verdict"), ("ok", "caution", "weak"), "caution"),
            "headline": str(answer.get("headline") or "")[:400],
            "strengths": _strings(answer.get("strengths")),
            "weaknesses": _strings(answer.get("weaknesses")),
            "patterns": _strings(answer.get("patterns")),
            "improvements": [
                {
                    "title": str(i.get("title") or "")[:160],
                    "detail": str(i.get("detail") or "")[:800],
                    "expected_effect": str(i.get("expected_effect") or "")[:400],
                    "how_to_validate": str(i.get("how_to_validate") or "")[:400],
                    "priority": _choice(i.get("priority"), ("high", "medium", "low"), "medium"),
                }
                for i in (answer.get("improvements") or [])[:6]
                if isinstance(i, dict) and i.get("title")
            ],
            "risks": _strings(answer.get("risks")),
            "reasoning": reply["reasoning"][:2500],
            "elapsed": reply["elapsed"],
        }

    # ------------------------------------------------------------------ OCR

    def ocr(self, filename: str, payload: bytes, principal, account_id: str | None = None) -> dict:
        if len(payload) > MAX_OCR_BYTES:
            raise ValueError("Файл больше 15 МБ")
        company = {}
        if account_id:
            self.svc.authorize(principal, account_id)
            account = self.svc.store.account(account_id)
            company = {
                "name": account["name"],
                "inn": company_inns().get(account_id.split("-")[-1], "") or None,
            }
        kind = _kind(payload)
        text, images = "", []
        if kind == "pdf":
            text = _pdf_text(payload)
            if len(text.strip()) < 40:
                images = _pdf_images(payload)
                text = "\n".join(_tesseract(image) for image in images)
        elif kind == "image":
            images = [payload]
            text = _tesseract(payload)
        elif kind == "text":
            text = payload.decode("utf-8", errors="replace")
        else:
            raise ValueError("Поддерживаются PDF, PNG, JPEG и текст")
        engine = "tesseract + llm"
        instruction = {"company": company, "file": Path(filename or "document").name[:120]}
        if len(text.strip()) >= 40:
            content = (
                "Запрос:\n```json\n"
                + json.dumps(instruction, ensure_ascii=False)
                + "\n```\nТекст документа после OCR:\n```\n"
                + text[:12000]
                + "\n```"
            )
        elif images:
            # Too little text: the model reads the image itself.
            engine = "llm vision"
            content = [
                {
                    "type": "text",
                    "text": "Запрос:\n```json\n"
                    + json.dumps(instruction, ensure_ascii=False)
                    + "\n```",
                },
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64," + _small_png(images[0])},
                },
            ]
        else:
            raise ValueError("В документе не найден текст")
        fields, reply = self.client.json(
            [{"role": "system", "content": prompt("ocr")}, {"role": "user", "content": content}],
            max_tokens=5000,
        )
        fields = _clean_fields(fields)
        return {
            "engine": engine,
            "text": text.strip()[:3000],
            "fields": fields,
            "drafts": _drafts(fields),
            "elapsed": reply["elapsed"],
        }

    # ------------------------------------------------------------------ chat agent

    def chat(self, messages: list[dict], context: dict, principal, progress=None) -> dict:
        """One answer of the agent. `progress(event)` reports the stage, each tool and the
        model's thinking while it works, for the live status in the chat."""
        report = progress or (lambda event: None)
        state = _ChatState(self, principal, context, report)
        today = state.as_of or date.today()
        system = (
            prompt("agent")
            + "\n\n## Текущее состояние\n"
            + json.dumps(
                {
                    "date_of_calculation": str(today),
                    "account_id": state.account_id,
                    "account_name": state.account_name(),
                    "page": context.get("path"),
                    "model": state.model,
                },
                ensure_ascii=False,
            )
        )
        history = [
            {"role": m["role"], "content": str(m["content"])[:4000]}
            for m in messages[-12:]
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        if not history or history[-1]["role"] != "user":
            raise ValueError("Нужно сообщение пользователя")
        talk = [{"role": "system", "content": system}, *history]
        reasoning, elapsed = [], 0.0
        for step in range(MAX_TOOL_STEPS + 1):
            report({"stage": "Думаю над вопросом" if step == 0 else "Обдумываю данные"})
            reply = self.client.chat(talk, tools=TOOLS, max_tokens=2500)
            elapsed += reply["elapsed"]
            if reply["reasoning"]:
                reasoning.append(reply["reasoning"][:800])
                report({"reasoning": reply["reasoning"][:800]})
            calls = reply["tool_calls"]
            if not calls or len(state.actions) >= MAX_TOOL_STEPS:
                text = reply["content"] or "Готово."
                break
            talk.append({"role": "assistant", "content": reply["content"], "tool_calls": calls})
            for call in calls:
                result = state.run(call["function"]["name"], call["function"].get("arguments"))
                talk.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(result, ensure_ascii=False, default=str)[:6000],
                    }
                )
        else:
            text = "Не успела закончить: слишком много шагов. Уточните вопрос."
        report({"stage": "Ответ готов"})
        return {
            "reply": text,
            "actions": state.actions,
            "ui": state.ui,
            "account_id": state.account_id,
            "as_of": state.as_of,
            "reasoning": "\n\n".join(reasoning)[:2500],
            "elapsed": round(elapsed, 1),
        }


class ChatJobs:
    """Chat answers in the background: the client starts a job and polls its stage, tools and
    thinking, so the user sees that the model is working and for how long."""

    TTL_SECONDS = 600

    def __init__(self, assistant: "Assistant"):
        self.assistant = assistant
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, messages: list[dict], context: dict, principal) -> str:
        if not messages or messages[-1].get("role") != "user":
            raise ValueError("Нужно сообщение пользователя")
        job_id = uuid.uuid4().hex
        job = {
            "owner": principal["id"],
            "status": "running",
            "started": time.monotonic(),
            "finished": None,
            "stage": "Читаю вопрос",
            "actions": [],
            "reasoning": [],
            "result": None,
            "error": None,
        }
        with self._lock:
            self._sweep()
            self._jobs[job_id] = job

        def progress(event):
            with self._lock:
                if "stage" in event:
                    job["stage"] = event["stage"]
                if "action" in event:
                    job["actions"].append(event["action"])
                if "reasoning" in event:
                    job["reasoning"].append(event["reasoning"])

        def work():
            try:
                result = self.assistant.chat(messages, context, principal, progress)
                outcome = {"status": "done", "result": result}
            except (LLMUnavailable, ValueError, PermissionError) as exc:
                outcome = {"status": "error", "error": str(exc)[:300]}
            except Exception:  # noqa: BLE001 — the client must not poll a job forever.
                outcome = {"status": "error", "error": "Ассистент споткнулся, попробуйте ещё раз"}
            with self._lock:
                job.update(outcome, finished=time.monotonic())

        threading.Thread(target=work, name=f"chat-{job_id[:8]}", daemon=True).start()
        return job_id

    def get(self, job_id: str, principal) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job["owner"] != principal["id"]:
                raise ValueError("Задача не найдена")
            end = job["finished"] or time.monotonic()
            return {
                "job_id": job_id,
                "status": job["status"],
                "elapsed": round(end - job["started"], 1),
                "stage": job["stage"],
                "actions": list(job["actions"]),
                "reasoning": list(job["reasoning"]),
                "result": job["result"],
                "error": job["error"],
            }

    def _sweep(self):
        now = time.monotonic()
        for key in [k for k, j in self._jobs.items() if now - j["started"] > self.TTL_SECONDS]:
            del self._jobs[key]


# ---------------------------------------------------------------------- chat tools

# Reading goes through the MCP connectors (mcp_server.py); here are only the actions.
ACTION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }
    for name, description, props, required in [
        (
            "run_scenario",
            "Сценарий «что если» к текущему прогнозу без изменения данных.",
            {
                "kind": {
                    "type": "string",
                    "enum": [
                        "revenue_drop",
                        "extra_expense",
                        "fixed_cost_increase",
                        "client_delay",
                    ],
                },
                "revenue_drop_pct": {"type": "number"},
                "amount_rub": {"type": "number", "description": "Сумма внепланового расхода"},
                "expense_date": {"type": "string", "description": "YYYY-MM-DD"},
                "fixed_cost_pct": {"type": "number"},
                "counterparty": {"type": "string", "description": "Клиент: название или ИНН"},
                "delay_days": {"type": "integer"},
            },
            ["kind"],
        ),
        (
            "add_payment",
            "Добавить в прогноз ожидаемое поступление (in) или платёж (out).",
            {
                "direction": {"type": "string", "enum": ["in", "out"]},
                "amount_rub": {"type": "number"},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "label": {"type": "string", "description": "Короткое описание"},
                "counterparty": {"type": "string"},
                "certainty": {"type": "string", "enum": ["promised", "agreed", "documented"]},
            },
            ["direction", "amount_rub", "date", "label"],
        ),
        (
            "cancel_payment",
            "Удалить из прогноза платёж из календаря по его id (id — из get_payment_calendar).",
            {"event_id": {"type": "string"}},
            ["event_id"],
        ),
        (
            "apply_tax_calendar",
            "Добавить в прогноз налоговые платежи по налоговому календарю счёта.",
            {},
            [],
        ),
        (
            "review_forecast",
            "ИИ-проверка текущего прогноза: надёжность, проверки, рекомендации, улучшения.",
            {},
            [],
        ),
        (
            "switch_account",
            "Переключить сервис на другую компанию (счёт).",
            {"account_id": {"type": "string"}},
            ["account_id"],
        ),
        (
            "open_page",
            "Открыть пользователю страницу сервиса.",
            {
                "page": {
                    "type": "string",
                    "enum": [
                        "forecast",
                        "gap",
                        "scenarios",
                        "calendar",
                        "operations",
                        "plan-fact",
                        "sources",
                        "debug",
                    ],
                }
            },
            ["page"],
        ),
    ]
]
TOOLS = openai_tools() + ACTION_TOOLS

TOOL_TITLES = {
    "run_scenario": "Проверила сценарий",
    "add_payment": "Добавила платёж",
    "cancel_payment": "Удалила платёж",
    "apply_tax_calendar": "Добавила налоговые платежи",
    "review_forecast": "Проверила прогноз с ИИ",
    "switch_account": "Переключила счёт",
    "open_page": "Открыла страницу",
}

# What the live status says while a tool runs.
TOOL_STAGES = {
    "run_scenario": "Считаю сценарий",
    "add_payment": "Добавляю платёж и пересчитываю прогноз",
    "cancel_payment": "Удаляю платёж и пересчитываю прогноз",
    "apply_tax_calendar": "Добавляю налоги и пересчитываю прогноз",
    "review_forecast": "Проверяю прогноз",
    "switch_account": "Переключаю счёт",
    "open_page": "Открываю страницу",
}


class _ChatState:
    """What the agent knows during one answer: account, date, the current run."""

    def __init__(self, assistant: Assistant, principal, context: dict, report=None):
        self.assistant = assistant
        self.svc = assistant.svc
        self.principal = principal
        self.report = report or (lambda event: None)
        self.account_id = context.get("account_id")
        self.as_of = _date(context.get("as_of"))
        self.model = context.get("model") or "ensemble"
        self.run_id = context.get("run_id")
        self.mcp = MCPServer(self.svc)
        self.reader = Reader(self.svc, principal)
        self.actions: list[dict] = []
        self.ui: list[dict] = []

    def account_name(self):
        if not self.account_id:
            return None
        try:
            return self.svc.store.account(self.account_id)["name"]
        except ValueError:
            return None

    def run(self, name: str, raw_args) -> dict:
        try:
            args = json.loads(raw_args or "{}") if isinstance(raw_args, str) else (raw_args or {})
        except json.JSONDecodeError:
            args = {}
        spec = MCP_TOOLS.get(name)
        title = f"MCP · {spec['title']}" if spec else TOOL_TITLES.get(name, name)
        self.report({"stage": f"{spec['title']}…" if spec else f"{TOOL_STAGES.get(name, name)}…"})
        try:
            if spec:
                result = self._mcp(name, spec, args)
            else:
                handler = getattr(self, f"tool_{name}", None)
                if handler is None:
                    raise ValueError(f"Нет инструмента {name}")
                result = handler(**args)
            action = {"tool": name, "title": title, "ok": True}
            self.actions.append(action)
            self.report({"action": action})
            return result
        except (ValueError, PermissionError, TypeError, LLMUnavailable, ToolError) as exc:
            action = {"tool": name, "title": title, "ok": False, "error": str(exc)[:200]}
            self.actions.append(action)
            self.report({"action": action})
            return {"error": str(exc)[:300]}

    def _mcp(self, name, spec, args):
        """MCP read connector, with the screen's account, date and run filled in."""
        props = spec["inputSchema"]["properties"]
        if "account_id" in props and not args.get("account_id"):
            args["account_id"] = self.account_id
        same = args.get("account_id") == self.account_id
        if "as_of" in props and not args.get("as_of") and same and self.as_of:
            args["as_of"] = str(self.as_of)
        if "run_id" in props and not args.get("run_id") and same and self.run_id:
            args["run_id"] = self.run_id
        result = self.mcp.call(name, args, self.principal, self.reader)
        if name == "get_forecast" and same:
            self.run_id, self.as_of = result["run_id"], _date(result["as_of"])
        return result

    # -- helpers

    def _account(self, account_id=None):
        account_id = account_id or self.account_id
        if not account_id:
            raise ValueError("Счёт не выбран: назовите компанию")
        self.svc.authorize(self.principal, account_id)
        return account_id

    def _forecast(self):
        if self.run_id:
            try:
                forecast = self.svc.get_forecast(self.run_id)
                if forecast.account_id == self.account_id:
                    return forecast
            except ValueError:
                pass
        account_id = self._account()
        as_of = self.as_of or _date(self.svc.store.quality(account_id).get("last_date"))
        forecast = self.svc.forecast(
            ForecastRequest(account_id=account_id, as_of=as_of, model=self.model)
        )
        self.run_id, self.as_of = forecast.run_id, forecast.as_of
        return forecast

    def _refreshed(self):
        result = self.svc.refresh(self.run_id, self.principal)
        self.run_id = result["forecast"]["run_id"]
        self.ui.append({"type": "refresh"})
        forecast = self.svc.get_forecast(self.run_id)
        risk = forecast.risk
        low = min(forecast.points, key=lambda p: p.closing_balance_minor)
        return {
            "changes": result["changes"],
            "forecast": {
                "first_gap_date": str(risk.first_gap_date) if risk.first_gap_date else None,
                "liquidity_need": rub(risk.liquidity_need_minor),
                "minimum": {"date": str(low.date), "balance": rub(low.closing_balance_minor)},
                "balance_day_30": rub(forecast.points[-1].closing_balance_minor),
                "probability_30d": risk.probability_30d,
            },
        }

    # -- actions

    def tool_run_scenario(
        self,
        kind,
        revenue_drop_pct=20,
        amount_rub=0,
        expense_date=None,
        fixed_cost_pct=10,
        counterparty=None,
        delay_days=14,
    ):
        forecast = self._forecast()
        spec = ScenarioSpec(
            base_run_id=forecast.run_id,
            kind=kind,
            revenue_drop_pct=revenue_drop_pct,
            amount_minor=int(round(float(amount_rub or 0) * 100)),
            expense_date=_date(expense_date),
            fixed_cost_pct=fixed_cost_pct,
            counterparty=counterparty,
            delay_days=delay_days,
        )
        result = self.svc.evaluate_scenario(spec)
        low = min(result.points, key=lambda p: p.closing_balance_minor)
        return {
            "scenario": kind,
            "first_gap_date": str(result.risk.first_gap_date)
            if result.risk.first_gap_date
            else None,
            "base_first_gap_date": str(result.base_first_gap_date)
            if result.base_first_gap_date
            else None,
            "liquidity_need": rub(result.risk.liquidity_need_minor),
            "need_change": rub(result.delta_liquidity_minor),
            "balance_day_30_change": rub(result.delta_end_minor),
            "minimum": {"date": str(low.date), "balance": rub(low.closing_balance_minor)},
            "assumptions": result.assumptions[:4],
        }

    def tool_add_payment(
        self, direction, amount_rub, date, label, counterparty=None, certainty="promised"
    ):
        forecast = self._forecast()
        day = _date(date)
        if day is None or day <= forecast.as_of:
            raise ValueError("Дата платежа должна быть после даты расчёта")
        amount = int(round(float(amount_rub) * 100))
        event = PlannedEvent(
            id=f"chat-{uuid.uuid4().hex[:12]}",
            account_id=forecast.account_id,
            component_id="manual",
            category="customer_payment" if direction == "in" else "operating_expense",
            amount_minor=amount,
            direction=direction,
            due_date=day,
            expected_date=day,
            known_at=forecast.as_of,
            source="manual",
            status="confirmed",
            label=str(label)[:200] or "Платёж из чата",
            counterparty_name=counterparty,
            certainty=certainty if certainty in CERTAINTY_LABELS else "promised",
        )
        self.svc.add_event(EventCreate(event=event, base_run_id=forecast.run_id), self.principal)
        return {
            "added": {"id": event.id, "amount": rub(amount), "date": str(day)}
        } | self._refreshed()

    def tool_cancel_payment(self, event_id):
        forecast = self._forecast()
        event = self.svc.cancel_event(event_id, forecast.as_of, self.principal)
        return {"cancelled": {"id": event.id, "label": event.label}} | self._refreshed()

    def tool_apply_tax_calendar(self):
        forecast = self._forecast()
        result = self.svc.apply_tax_calendar(forecast.account_id, forecast.run_id, self.principal)
        added = {"created": result.get("created", 0), "warning": result.get("warning")}
        return added | self._refreshed()

    def tool_review_forecast(self):
        review = self.assistant.review(self._forecast().run_id, self.principal)
        return {
            k: review[k]
            for k in ("verdict", "headline", "trust", "recommendations", "improvements")
        }

    def tool_switch_account(self, account_id):
        account_id = self._account(account_id)
        last = _date(self.svc.store.quality(account_id).get("last_date"))
        self.account_id, self.as_of, self.run_id = account_id, last, None
        self.ui.append({"type": "switch_account", "account_id": account_id, "as_of": str(last)})
        return {"account_id": account_id, "name": self.account_name(), "as_of": str(last)}

    def tool_open_page(self, page):
        allowed = {
            "forecast",
            "gap",
            "scenarios",
            "calendar",
            "operations",
            "plan-fact",
            "sources",
            "debug",
        }
        if page not in allowed:
            raise ValueError("Такой страницы нет")
        self.ui.append({"type": "navigate", "path": f"/{page}"})
        return {"opened": page}


# ---------------------------------------------------------------------- helpers


def _date(value):
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def evaluation_context(report: dict, result: dict) -> dict:
    """What the model reviewer reads after the whole-dataset check: metrics and extreme companies."""
    names = {a["account_id"]: a for a in report["accounts"]}
    metrics = result["metrics"]
    rated = sorted(
        (r for r in result["companies"] if r.get("skill") is not None), key=lambda r: r["skill"]
    )

    def row(r):
        account = names.get(r["account_id"], {})
        return {
            "company": account.get("company_id"),
            "operations": account.get("operations"),
            "history": f"{account.get('first_date')} — {account.get('last_date')}",
            "balance_mismatches": account.get("inconsistent_transitions"),
            "skill": r["skill"],
            "model_mae": rub(r["mae_minor"]),
            "constant_mae": rub(r["constant_mae_minor"]),
            "windows": r["windows"],
            "gap_now": r.get("gap_date"),
            "model": r.get("model"),
        }

    money = ("_minor",)
    balance = {
        k: (rub(v) if k.endswith(money) and v is not None else v)
        for k, v in metrics["balance"].items()
        if k not in ("by_horizon", "mse", "constant_mse")
    }
    return {
        "dataset": {k: report[k] for k in ("companies", "rows", "accepted", "rejected")}
        | {"period": f"{report['first_date']} — {report['last_date']}"}
        | {"quality": report["quality"], "unknown_share": report["categories"]["unknown_share"]},
        "summary": {
            "method": "backtest: up to 4 origins per company every 14 days, 30-day horizon, "
            "truth D3 (balance without new financing), gap label D2",
            "balance": balance,
            "gaps_alarm_rule": metrics["gaps"],
            "gaps_trajectory_rule": metrics.get("gaps_trajectory"),
            "probability": metrics["probability"],
            "models": result["models"],
            "current": result["current"],
        },
        "worst": [row(r) for r in rated[:6]],
        "best": [row(r) for r in rated[-4:]],
        "model_card": MODEL_CARD,
    }


def _choice(value, options, default):
    return value if value in options else default


def _strings(values, limit=6):
    return [str(v)[:400] for v in (values or [])[:limit] if v]


def _clean_review(answer: dict) -> dict:
    return {
        "verdict": _choice(answer.get("verdict"), ("ok", "caution", "weak"), "caution"),
        "headline": str(answer.get("headline") or "")[:400],
        "trust": str(answer.get("trust") or "")[:600],
        "checks": [
            {
                "title": str(c.get("title") or "")[:160],
                "status": _choice(c.get("status"), ("ok", "warning", "problem"), "warning"),
                "detail": str(c.get("detail") or "")[:600],
            }
            for c in (answer.get("checks") or [])[:8]
            if isinstance(c, dict) and c.get("title")
        ],
        "recommendations": [
            {
                "title": str(r.get("title") or "")[:160],
                "detail": str(r.get("detail") or "")[:700],
                "priority": _choice(r.get("priority"), ("high", "medium", "low"), "medium"),
                "deadline": str(_date(r.get("deadline")) or "") or None,
                "amount_minor": int(r["amount_minor"])
                if isinstance(r.get("amount_minor"), (int, float)) and r["amount_minor"] > 0
                else None,
            }
            for r in (answer.get("recommendations") or [])[:6]
            if isinstance(r, dict) and r.get("title")
        ],
        "improvements": [
            {"title": str(i.get("title") or "")[:160], "detail": str(i.get("detail") or "")[:600]}
            for i in (answer.get("improvements") or [])[:5]
            if isinstance(i, dict) and i.get("title")
        ],
    }


def _kind(payload: bytes) -> str:
    if payload.startswith(b"%PDF"):
        return "pdf"
    if payload[:8] == b"\x89PNG\r\n\x1a\n" or payload[:3] == b"\xff\xd8\xff":
        return "image"
    if payload[:4] in (b"RIFF", b"GIF8") or payload[:2] in (b"BM", b"II", b"MM"):
        return "image"
    try:
        payload[:2000].decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "unknown"


def _tesseract(image: bytes) -> str:
    try:
        done = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", "rus+eng", "--psm", "4"],
            input=image,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("OCR недоступен на сервере") from exc
    return done.stdout.decode("utf-8", errors="replace")


def _pdf_text(payload: bytes) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "doc.pdf"
        path.write_bytes(payload)
        try:
            done = subprocess.run(
                ["pdftotext", "-layout", "-l", "5", str(path), "-"],
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return done.stdout.decode("utf-8", errors="replace")


def _pdf_images(payload: bytes) -> list[bytes]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "doc.pdf"
        path.write_bytes(payload)
        try:
            subprocess.run(
                ["pdftoppm", "-r", "200", "-l", "3", "-png", str(path), str(Path(tmp) / "page")],
                capture_output=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("Не удалось прочитать PDF") from exc
        return [p.read_bytes() for p in sorted(Path(tmp).glob("page*.png"))]


def _small_png(image: bytes) -> str:
    from PIL import Image

    with Image.open(io.BytesIO(image)) as picture:
        picture = picture.convert("RGB")
        picture.thumbnail((1600, 1600))
        buffer = io.BytesIO()
        picture.save(buffer, "PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode()


def _clean_fields(fields: dict) -> dict:
    def party(value):
        value = value if isinstance(value, dict) else {}
        inn = "".join(ch for ch in str(value.get("inn") or "") if ch.isdigit())
        return {
            "name": str(value.get("name") or "")[:200] or None,
            "inn": inn if len(inn) in (10, 12) else None,
        }

    def minor(value):
        return int(value) if isinstance(value, (int, float)) and value > 0 else None

    return {
        "doc_type": _choice(
            fields.get("doc_type"),
            ("invoice", "contract", "act", "upd", "payment_order", "other"),
            "other",
        ),
        "number": str(fields.get("number") or "")[:80] or None,
        "doc_date": str(_date(fields.get("doc_date")) or "") or None,
        "seller": party(fields.get("seller")),
        "buyer": party(fields.get("buyer")),
        "direction": _choice(fields.get("direction"), ("in", "out"), None),
        "amount_minor": minor(fields.get("amount_minor")),
        "vat_minor": minor(fields.get("vat_minor")),
        "due_date": str(_date(fields.get("due_date")) or "") or None,
        "subject": str(fields.get("subject") or "")[:200] or None,
        "schedule": [
            {
                "date": str(_date(s.get("date")) or "") or None,
                "amount_minor": minor(s.get("amount_minor")),
                "label": str(s.get("label") or "")[:120] or None,
            }
            for s in (fields.get("schedule") or [])[:6]
            if isinstance(s, dict) and minor(s.get("amount_minor"))
        ],
        "confidence": max(0.0, min(1.0, float(fields.get("confidence") or 0))),
        "notes": str(fields.get("notes") or "")[:400] or None,
    }


def _drafts(fields: dict) -> list[dict]:
    """Payments ready for the event form: one per schedule line, or the document total."""
    direction = fields["direction"]
    other = fields["buyer"] if direction == "in" else fields["seller"]
    name = (other or {}).get("name")
    title = {
        "invoice": "Счёт",
        "contract": "Договор",
        "act": "Акт",
        "upd": "УПД",
        "payment_order": "Платёжное поручение",
    }.get(fields["doc_type"], "Документ")
    base = f"{title} №{fields['number']}" if fields["number"] else title
    source = {"invoice": "invoice", "contract": "contract", "upd": "invoice"}.get(
        fields["doc_type"], "manual"
    )
    certainty = "agreed" if fields["doc_type"] == "contract" else "documented"
    lines = fields["schedule"] or [
        {"date": fields["due_date"], "amount_minor": fields["amount_minor"], "label": None}
    ]
    return [
        {
            "direction": direction,
            "amount_minor": line["amount_minor"],
            "date": line["date"],
            "label": " · ".join(p for p in (base, line["label"], name) if p)[:200],
            "counterparty_name": name,
            "counterparty_inn": (other or {}).get("inn"),
            "source": source,
            "certainty": certainty,
            "category": "customer_payment" if direction == "in" else "supplier",
        }
        for line in lines
        if line["amount_minor"]
    ]
