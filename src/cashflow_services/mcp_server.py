"""Kassandra MCP server: read-only «Get» connectors to the system over the Model Context Protocol.

Streamable HTTP with JSON responses (spec 2025-06-18): POST /mcp carries JSON-RPC 2.0 —
initialize, tools/list, tools/call, ping. The same tools serve outside MCP clients and the chat
agent, which calls them in process. Every tool checks access to the account and only reads:
forecasts it builds are ordinary immutable runs, nothing in the user's data changes.
"""

import json
from datetime import date, timedelta

from cashflow_domain.schemas import CATEGORY_LABELS, SOURCE_LABELS, ForecastRequest

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "kassandra", "title": "Kassandra — прогноз денег", "version": "1.1.0"}
INSTRUCTIONS = (
    "Данные о деньгах компаний малого бизнеса: счета, прогноз остатка на 30 дней, кассовые "
    "разрывы, платёжный календарь, операции и их структура. Суммы — в рублях; даты — YYYY-MM-DD; "
    "account_id — номер счёта (например, C004). Если as_of не указан, берётся последний день выписки."
)

# How the product model performs over all companies (docs/presentation_metrics.md, X10).
MODEL_CARD = {
    "name": "Ансамбль (X10): остаток на дату расчёта + взвешенные изменения трёх честных методов "
    "(HGB по потокам, HGB с календарём, профиль месяца); веса по горизонту, затухание к «не изменится»",
    "trained_to": "модели до 2025-12-31, веса до 2026-02-28; будущее не используется",
    "protocol": "121 компания, 14 недельных срезов март–май 2026 и контроль июль–август, "
    "сравнение с прогнозом «остаток не меняется»",
    "trajectory_skill": "+0,02 на выборе, +0,027 [+0,016; +0,048] на контроле: чуть лучше ориентира",
    "minimum_skill": "+0,24 / +0,28: самый низкий остаток месяца на четверть точнее ориентира",
    "gap_recall": "находит 13 из 69 разрывов с датой ±3 дня за 7+ дней, медиана — за 22 дня; "
    "0,24 ложной тревоги на счёт-месяц",
    "interval": "фактический остаток внутри P10–P90 в 74–78% дней (номинал 80%)",
    "probability": "X11: изотоническая калибровка на срезе выбора (метка D2), тревога от 26%; "
    "на контроле июль–август precision 0,44, recall 0,55, F1 0,49, Brier skill около +0,20; "
    "для дат до 24.06.2026 — сырая частота траекторий, экспериментальная",
}


class ToolError(ValueError):
    """Shown to the caller as a tool result with isError, not as a protocol error."""


def rub(minor) -> str:
    if minor is None:
        return "—"
    sign = "−" if minor < 0 else ""
    return f"{sign}{abs(int(minor)) // 100:,} ₽".replace(",", " ")


def _date(value):
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ToolError(f"Дата {value!r} не в формате YYYY-MM-DD") from exc


TOOLS: dict[str, dict] = {}

ACCOUNT = {"account_id": {"type": "string", "description": "Номер счёта, например C004"}}
AS_OF = {"as_of": {"type": "string", "description": "Дата расчёта YYYY-MM-DD"}}
RUN = {"run_id": {"type": "string", "description": "Готовый расчёт прогноза, если он есть"}}


def tool(name, title, description, properties=None, required=()):
    def register(handler):
        TOOLS[name] = {
            "name": name,
            "title": title,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties or {},
                "required": list(required),
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
            "handler": handler,
        }
        return handler

    return register


class Reader:
    """One caller: service, access rights and a forecast cache for the request."""

    def __init__(self, svc, principal):
        self.svc, self.principal = svc, principal
        self._runs: dict = {}

    def account(self, account_id):
        if not account_id:
            raise ToolError("Нужен account_id: номер счёта")
        self.svc.authorize(self.principal, account_id)
        return self.svc.store.account(account_id)

    def last_date(self, account_id) -> date:
        last = self.svc.store.quality(account_id).get("last_date")
        if not last:
            raise ToolError("У счёта нет выписки")
        return date.fromisoformat(last)

    def forecast(self, account_id, as_of=None, model=None, run_id=None):
        self.account(account_id)
        if run_id:
            forecast = self.svc.get_forecast(run_id)
            if forecast.account_id == account_id and (not as_of or str(forecast.as_of) == as_of):
                return forecast
        day = _date(as_of) or self.last_date(account_id)
        key = (account_id, day, model or "ensemble")
        if key not in self._runs:
            self._runs[key] = self.svc.forecast(
                ForecastRequest(account_id=account_id, as_of=day, model=model or "ensemble")
            )
        return self._runs[key]


@tool(
    "get_accounts",
    "Компании",
    "Список компаний (счетов) пользователя с периодом выписки; поиск по названию или номеру.",
    {"query": {"type": "string", "description": "Часть названия или номера"}},
)
def get_accounts(r: Reader, query=""):
    query = (query or "").lower()
    found = [
        a
        for a in r.svc.accounts(r.principal)
        if a["owner"] != "debug" and (not query or query in f"{a['name']} {a['id']}".lower())
    ]
    return {
        "found": len(found),
        "accounts": [
            {
                "account_id": a["id"],
                "name": a["name"],
                "first_date": (a.get("quality") or {}).get("first_date"),
                "last_date": (a.get("quality") or {}).get("last_date"),
            }
            for a in found[:15]
        ],
    }


@tool(
    "get_account_overview",
    "Сводка по счёту",
    "Компания, период выписки, число операций, текущий остаток, качество данных и подключённые источники.",
    ACCOUNT,
    ("account_id",),
)
def get_account_overview(r: Reader, account_id):
    account = r.account(account_id)
    quality = r.svc.store.quality(account_id)
    daily = r.svc.store.daily(account_id)
    sources = r.svc.sources_summary(account_id, r.principal)
    return {
        "account_id": account_id,
        "name": account["name"],
        "period": f"{quality.get('first_date')} — {quality.get('last_date')}",
        "operations": quality.get("operations"),
        "balance_last_day": rub(daily[-1]["balance_minor"]) if daily else None,
        "data_quality": {
            "balance_mismatches": len(quality.get("inconsistent_transitions", [])),
            "masked_days": quality.get("invalid_days"),
            "empty_purpose": quality.get("empty_purpose"),
        },
        "registered_events_by_source": [
            {"source": s["label"], "events": s.get("events")}
            for s in sources.get("registered_events", [])
        ],
    }


@tool(
    "get_forecast",
    "Прогноз остатка",
    "Прогноз остатка на 30 дней: остаток сегодня, через 7/14/30 дней, минимум, дата кассового "
    "разрыва, нехватка, вероятность и крупнейшие ожидаемые платежи.",
    {
        **ACCOUNT,
        **AS_OF,
        **RUN,
        "model": {"type": "string", "enum": ["ensemble", "hgb", "weekday"]},
    },
    ("account_id",),
)
def get_forecast(r: Reader, account_id, as_of=None, run_id=None, model=None):
    f = r.forecast(account_id, as_of, model, run_id)
    points = f.points
    low = min(points, key=lambda p: p.closing_balance_minor)
    upcoming = sorted(
        (e for e in f.events if e.status != "cancelled"), key=lambda e: -e.remaining_minor
    )[:8]
    return {
        "account_id": account_id,
        "run_id": f.run_id,
        "as_of": str(f.as_of),
        "model": f.model,
        "balance_today": rub(f.opening_balance_minor),
        "balance_day_7": rub(points[6].closing_balance_minor),
        "balance_day_14": rub(points[13].closing_balance_minor),
        "balance_day_30": rub(points[-1].closing_balance_minor),
        "minimum": {"date": str(low.date), "balance": rub(low.closing_balance_minor)},
        "first_gap_date": str(f.risk.first_gap_date) if f.risk.first_gap_date else None,
        "liquidity_need": rub(f.risk.liquidity_need_minor),
        "probability_30d": f.risk.probability_30d,
        "probability_status": "экспериментальная оценка",
        "inflow_30d": rub(sum(p.event_in_minor + p.residual_in_minor for p in points)),
        "outflow_30d": rub(sum(p.event_out_minor + p.residual_out_minor for p in points)),
        "largest_payments": [
            {
                "date": str(e.expected_date),
                "direction": e.direction,
                "amount": rub(e.remaining_minor),
                "label": e.label[:80],
                "source": SOURCE_LABELS.get(e.source, e.source),
            }
            for e in upcoming
        ],
    }


@tool(
    "get_gap_explanation",
    "Причины разрыва",
    "Почему возникает кассовый разрыв и какие действия предлагает расчёт.",
    {**ACCOUNT, **AS_OF, **RUN},
    ("account_id",),
)
def get_gap_explanation(r: Reader, account_id, as_of=None, run_id=None):
    explanation = r.svc.explain(run_id=r.forecast(account_id, as_of, run_id=run_id).run_id)
    return {
        "has_gap": explanation.has_gap,
        "when": explanation.when,
        "shortfall": explanation.shortfall,
        "why": explanation.why,
        "actions": [a.text for a in explanation.actions],
    }


@tool(
    "get_payment_calendar",
    "Платёжный календарь",
    "Известные и ожидаемые платежи и поступления по дням от даты расчёта.",
    {
        **ACCOUNT,
        **AS_OF,
        **RUN,
        "days": {"type": "integer", "description": "Дней вперёд, 1–30"},
        "direction": {"type": "string", "enum": ["in", "out"]},
        "obligatory_only": {"type": "boolean"},
    },
    ("account_id",),
)
def get_payment_calendar(
    r: Reader, account_id, as_of=None, run_id=None, days=30, direction=None, obligatory_only=False
):
    f = r.forecast(account_id, as_of, run_id=run_id)
    filters = {"date_to": f.as_of + timedelta(days=max(1, min(int(days or 30), 30)))}
    if direction in ("in", "out"):
        filters["direction"] = [direction]
    if obligatory_only:
        filters["obligatory"] = True
    view = r.svc.calendar_view(f.run_id, r.principal, filters)
    return {
        "totals": {
            "payments": view["totals"]["count"],
            "inflow": rub(view["totals"]["inflow_minor"]),
            "outflow": rub(view["totals"]["outflow_minor"]),
        },
        "items": [
            {
                "id": i["id"],
                "date": str(i["date"]),
                "direction": i["direction"],
                "amount": rub(i["amount_minor"]),
                "title": i["title"][:90],
                "category": i["category_label"],
                "source": i["source_short"],
                "obligatory": i["obligatory"],
                "kind": i["kind"],
            }
            for i in view["items"][:25]
        ],
    }


@tool(
    "get_transactions",
    "Операции по выписке",
    "Прошедшие операции с фильтрами: период, направление, категория, контрагент, поиск по назначению.",
    {
        **ACCOUNT,
        "date_from": {"type": "string"},
        "date_to": {"type": "string"},
        "direction": {"type": "string", "enum": ["in", "out"]},
        "category": {"type": "string", "description": "Код категории, например supplier"},
        "query": {"type": "string", "description": "Поиск по контрагенту, ИНН, назначению"},
        "sort": {"type": "string", "enum": ["date_desc", "date_asc", "amount_desc", "amount_asc"]},
        "limit": {"type": "integer", "description": "Не больше 30"},
    },
    ("account_id",),
)
def get_transactions(
    r: Reader,
    account_id,
    date_from=None,
    date_to=None,
    direction=None,
    category=None,
    query=None,
    sort=None,
    limit=15,
):
    r.account(account_id)
    filters = {
        "date_from": _date(date_from),
        "date_to": _date(date_to) or r.last_date(account_id),
        "direction": direction if direction in ("in", "out") else None,
        "category": [category] if category else None,
        "q": query or None,
        "sort": sort or "date_desc",
    }
    page = r.svc.transactions_view(
        account_id, r.principal, filters, 1, max(1, min(int(limit or 15), 30))
    )
    totals = page.get("totals") or {}
    return {
        "found": page["total"],
        "inflow": rub(totals.get("inflow_minor")),
        "outflow": rub(totals.get("outflow_minor")),
        "items": [
            {
                "date": t["operation_date"],
                "direction": t["direction"],
                "amount": rub(t["amount_minor"]),
                "counterparty": t["counterparty_name"],
                "category": CATEGORY_LABELS.get(t["category"], t["category"]),
                "purpose": (t["payment_purpose"] or "")[:120],
            }
            for t in page["items"]
        ],
    }


@tool(
    "get_category_summary",
    "Структура операций",
    "Поступления и списания по категориям и группам за прошедший период.",
    {
        **ACCOUNT,
        **AS_OF,
        "days": {"type": "integer", "description": "Сколько дней назад, по умолчанию 90"},
        "direction": {"type": "string", "enum": ["in", "out"]},
    },
    ("account_id",),
)
def get_category_summary(r: Reader, account_id, as_of=None, days=90, direction=None):
    r.account(account_id)
    end = _date(as_of) or r.last_date(account_id)
    filters = {"direction": direction} if direction in ("in", "out") else None
    summary = r.svc.categories_summary(
        account_id, end - timedelta(days=max(7, min(int(days or 90), 365)) - 1), end, filters
    )
    return {
        "period": f"{summary['date_from']} — {summary['date_to']}",
        "inflow": rub(summary["inflow_minor"]),
        "outflow": rub(summary["outflow_minor"]),
        "categories": [
            {
                "direction": row["direction"],
                "label": row["label"],
                "amount": rub(row["amount_minor"]),
                "share": row.get("share"),
            }
            for row in summary["rows"][:14]
        ],
    }


@tool(
    "get_top_clients",
    "Крупнейшие клиенты",
    "Клиенты, от которых пришло больше всего денег за период, и их ожидаемые платежи.",
    {**ACCOUNT, **AS_OF, "days": {"type": "integer", "description": "По умолчанию 90"}},
    ("account_id",),
)
def get_top_clients(r: Reader, account_id, as_of=None, days=90):
    r.account(account_id)
    clients = r.svc.top_clients(
        account_id, _date(as_of) or r.last_date(account_id), 10, max(7, min(int(days or 90), 365))
    )
    return {
        "clients": [
            {
                "name": c.get("counterparty_name"),
                "amount": rub(c.get("amount_minor")),
                "operations": c.get("operations"),
                "share": c.get("share"),
                "last_payment": str(c.get("last_date")),
            }
            for c in clients
        ]
    }


@tool(
    "get_recurring_payments",
    "Регулярные платежи",
    "Повторяющиеся поступления и списания из истории (зарплата, аренда, подписки) и их следующие даты.",
    {**ACCOUNT, **AS_OF},
    ("account_id",),
)
def get_recurring_payments(r: Reader, account_id, as_of=None):
    r.account(account_id)
    view = r.svc.recurring(account_id, _date(as_of) or r.last_date(account_id))
    return {
        "components": [
            {
                "label": c["label"],
                "direction": c["direction"],
                "rhythm": c.get("rhythm_label"),
                "amount": rub(c["amount_minor"]),
                "counterparty": c.get("counterparty_name"),
                "next_dates": [str(d) for d in (c.get("next_dates") or [])[:3]],
            }
            for c in view["components"][:15]
        ]
    }


@tool(
    "get_tax_calendar",
    "Налоговый календарь",
    "Налоговый режим, выведенный из истории, и ближайшие налоговые платежи (ЕНП, УСН, НДС).",
    {**ACCOUNT, **AS_OF},
    ("account_id",),
)
def get_tax_calendar(r: Reader, account_id, as_of=None):
    r.account(account_id)
    view = r.svc.tax_calendar(account_id, _date(as_of) or r.last_date(account_id))
    return {
        "regime": (view.get("regime") or {}).get("label"),
        "rules": view.get("rules", [])[:4],
        "events": [
            {"date": e["date"], "amount": rub(e["amount_minor"]), "label": e["label"]}
            for e in view.get("events", [])[:6]
        ],
    }


@tool(
    "get_plan_fact",
    "План и факт",
    "Сравнение прогноза с фактической выпиской после даты расчёта: отклонения и просроченные платежи.",
    {**ACCOUNT, **AS_OF, **RUN},
    ("account_id",),
)
def get_plan_fact(r: Reader, account_id, as_of=None, run_id=None):
    view = r.svc.plan_fact_view(r.forecast(account_id, as_of, run_id=run_id).run_id)
    summary = view.get("summary") or {}
    return {
        "days_compared": summary.get("days_compared"),
        "deviation_days": summary.get("deviation_days"),
        "mean_abs_deviation": rub(summary.get("mean_abs_delta_minor")),
        "max_abs_deviation": rub(summary.get("max_abs_delta_minor")),
        "overdue": [
            {"label": o["label"], "amount": rub(o["remaining_minor"]), "days": o["days_overdue"]}
            for o in view.get("overdue", [])[:6]
        ],
    }


@tool(
    "get_alerts",
    "Предупреждения",
    "Предупреждения по последнему прогнозу: разрыв, просрочки, рискованные контрагенты, стресс-сценарий.",
    {**ACCOUNT, **RUN},
    ("account_id",),
)
def get_alerts(r: Reader, account_id, run_id=None):
    r.account(account_id)
    view = r.svc.alerts(account_id, r.principal, run_id)
    return {
        "alerts": [
            {
                "severity": a["severity"],
                "title": a["title"],
                "text": a["text"],
                "date": str(a["date"]) if a.get("date") else None,
                "amount": rub(a.get("amount_minor")),
            }
            for a in view.get("alerts", [])[:8]
        ]
    }


@tool(
    "get_counterparty_risks",
    "Риски контрагентов",
    "Крупные контрагенты со статусом в реестре: ликвидация, банкротство, недостоверность.",
    {**ACCOUNT, **AS_OF},
    ("account_id",),
)
def get_counterparty_risks(r: Reader, account_id, as_of=None):
    r.account(account_id)
    risks = r.svc.counterparty_risks(
        account_id, _date(as_of) or r.last_date(account_id), r.principal
    )
    return {
        "checked": risks.get("checked"),
        "flagged": [
            {
                "name": c["counterparty_name"],
                "role": c["role"],
                "amount_90d": rub(c["amount_minor"]),
                "status": c["check"]["status_label"],
                "risk": c["check"]["risk"],
            }
            for c in risks.get("counterparties", [])
            if c["check"]["risk"] in ("critical", "warning")
        ],
    }


@tool(
    "get_balance_history",
    "История остатков",
    "Остатки на конец дня за прошедший период (по умолчанию 30 дней до последней выписки).",
    {**ACCOUNT, "date_from": {"type": "string"}, "date_to": {"type": "string"}},
    ("account_id",),
)
def get_balance_history(r: Reader, account_id, date_from=None, date_to=None):
    r.account(account_id)
    end = _date(date_to) or r.last_date(account_id)
    start = _date(date_from) or end - timedelta(days=29)
    days = [
        d for d in r.svc.store.daily(account_id) if start <= date.fromisoformat(d["date"]) <= end
    ]
    step = max(1, len(days) // 31)
    balances = [d["balance_minor"] for d in days]
    return {
        "period": f"{start} — {end}",
        "minimum": rub(min(balances)) if balances else None,
        "maximum": rub(max(balances)) if balances else None,
        "days": [
            {"date": d["date"], "balance": rub(d["balance_minor"]), "reliable": d["valid"]}
            for d in days[::step]
        ],
    }


@tool(
    "get_model_quality",
    "Качество модели",
    "Как точна модель прогноза по честной проверке на всех компаниях: что ей можно доверять.",
)
def get_model_quality(r: Reader):
    return MODEL_CARD


class MCPServer:
    def __init__(self, svc):
        self.svc = svc

    @staticmethod
    def list_tools() -> list[dict]:
        return [{k: v for k, v in t.items() if k != "handler"} for t in TOOLS.values()]

    def call(self, name: str, arguments: dict | None, principal, reader: Reader | None = None):
        """The structured result of one tool; ToolError and access errors pass up."""
        spec = TOOLS.get(name)
        if spec is None:
            raise ToolError(f"Нет инструмента {name}")
        allowed = spec["inputSchema"]["properties"]
        args = {k: v for k, v in (arguments or {}).items() if k in allowed and v is not None}
        missing = [k for k in spec["inputSchema"]["required"] if k not in args]
        if missing:
            raise ToolError("Не хватает параметров: " + ", ".join(missing))
        return spec["handler"](reader or Reader(self.svc, principal), **args)

    def handle(self, message, principal):
        """One JSON-RPC message; None for notifications (HTTP 202 without a body)."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, -32600, "Ожидается JSON-RPC 2.0")
        method, ident = message.get("method"), message.get("id")
        if ident is None:
            return None
        params = message.get("params") or {}
        if method == "initialize":
            asked = params.get("protocolVersion")
            return _result(
                ident,
                {
                    "protocolVersion": asked
                    if asked in PROTOCOL_VERSIONS
                    else PROTOCOL_VERSIONS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                    "instructions": INSTRUCTIONS,
                },
            )
        if method == "ping":
            return _result(ident, {})
        if method == "tools/list":
            return _result(ident, {"tools": self.list_tools()})
        if method == "tools/call":
            name = params.get("name")
            if name not in TOOLS:
                return _error(ident, -32602, f"Нет инструмента {name}")
            try:
                data = self.call(name, params.get("arguments"), principal)
            except (ToolError, ValueError, PermissionError) as exc:
                return _result(
                    ident, {"content": [{"type": "text", "text": str(exc)[:300]}], "isError": True}
                )
            text = json.dumps(data, ensure_ascii=False, default=str)
            return _result(
                ident,
                {
                    "content": [{"type": "text", "text": text}],
                    "structuredContent": json.loads(text),
                    "isError": False,
                },
            )
        return _error(ident, -32601, f"Метод {method} не поддерживается")


def _result(ident, result):
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def _error(ident, code, message):
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


def openai_tools() -> list[dict]:
    """MCP tool descriptors as OpenAI function specs for the chat agent."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": f"[MCP · {t['title']}] {t['description']}",
                "parameters": {
                    k: v for k, v in t["inputSchema"].items() if k != "additionalProperties"
                },
            },
        }
        for t in TOOLS.values()
    ]
