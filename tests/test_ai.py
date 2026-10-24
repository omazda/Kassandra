"""LLM features with a scripted client: review and its cache, chat tools, OCR drafts, budget."""

import json
from datetime import date

import httpx
import pytest
from conftest import OWNER

from cashflow_domain.schemas import ForecastRequest
from cashflow_services.ai import Assistant
from cashflow_services.llm import LLMClient, LLMSettings, LLMUnavailable, clean_text, parse_json

AS_OF = date(2026, 4, 30)
READY = LLMSettings(enabled=True, api_key="k", base_url="http://llm", model_id="mimo/test")


class Scripted(LLMClient):
    """Returns prepared replies in order and records what was sent."""

    def __init__(self, replies):
        super().__init__(READY)
        self.replies, self.sent = list(replies), []

    def chat(self, messages, *, tools=None, max_tokens=None, temperature=0.3):
        self.sent.append({"messages": messages, "tools": tools})
        reply = self.replies.pop(0)
        if isinstance(reply, dict) and "tool_calls" in reply:
            return {"content": "", "reasoning": "", "usage": {}, "elapsed": 0.1, **reply}
        content = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return {
            "content": content,
            "reasoning": "думаю",
            "tool_calls": [],
            "usage": {},
            "elapsed": 0.1,
        }


def call(name, **args):
    return {"id": f"c-{name}", "function": {"name": name, "arguments": json.dumps(args)}}


def forecast(svc):
    return svc.forecast(ForecastRequest(account_id="A", as_of=AS_OF, model="weekday"))


REVIEW = {
    "verdict": "caution",
    "headline": "Разрыва нет, но поступления не подтверждены",
    "trust": "Опираться с оговорками",
    "checks": [{"title": "Согласованность", "status": "ok", "detail": "Минимум положителен"}],
    "recommendations": [
        {"title": "Подтвердить оплату", "detail": "…", "priority": "urgent", "amount_minor": -5}
    ],
    "improvements": [{"title": "Добавить счета", "detail": "…"}],
}


def test_review_is_cleaned_cached_and_sees_only_known_facts(svc):
    run = forecast(svc)
    client = Scripted([f"Вот ответ:\n```json\n{json.dumps(REVIEW, ensure_ascii=False)}\n```"])
    ai = Assistant(svc, client)
    review = ai.review(run.run_id, OWNER)
    assert review["verdict"] == "caution" and not review["cached"]
    # Unknown priority and a negative sum are not passed to the client.
    assert review["recommendations"][0]["priority"] == "medium"
    assert review["recommendations"][0]["amount_minor"] is None
    sent = client.sent[0]["messages"][1]["content"]
    assert '"as_of": "2026-04-30"' in sent and "backtest" in sent
    again = ai.review(run.run_id, OWNER)
    assert again["cached"] and len(client.sent) == 1
    # A new run with the same inputs reuses the review instead of paying for another one.
    rerun = forecast(svc)
    assert rerun.run_id != run.run_id
    reused = ai.cached_review(rerun.run_id, OWNER)
    assert reused["cached"] and reused["run_id"] == rerun.run_id and "signature" not in reused
    with pytest.raises(PermissionError):
        ai.review(run.run_id, {"id": "bob", "accounts": []})


def test_chat_runs_tools_and_changes_the_forecast(svc):
    run = forecast(svc)
    client = Scripted(
        [
            {"tool_calls": [call("get_forecast")]},
            {
                "tool_calls": [
                    call(
                        "add_payment",
                        direction="in",
                        amount_rub=450000,
                        date="2026-05-15",
                        label="Оплата от клиента",
                    ),
                    call("open_page", page="calendar"),
                ]
            },
            "Добавила 450 000 ₽ на 15 мая.",
        ]
    )
    reply = Assistant(svc, client).chat(
        [{"role": "user", "content": "Добавь 450 тысяч 15 мая"}],
        {"account_id": "A", "as_of": "2026-04-30", "run_id": run.run_id},
        OWNER,
    )
    assert reply["reply"].startswith("Добавила")
    assert [a["tool"] for a in reply["actions"]] == ["get_forecast", "add_payment", "open_page"]
    assert all(a["ok"] for a in reply["actions"])
    assert {"type": "navigate", "path": "/calendar"} in reply["ui"] and {
        "type": "refresh"
    } in reply["ui"]
    added = [e for e in svc.events("A", AS_OF) if e.id.startswith("chat-")]
    assert added and added[0].amount_minor == 45_000_000 and added[0].known_at == AS_OF
    # The tool result reached the model as JSON with rubles, never a raw run dump.
    tool_messages = [m for m in client.sent[-1]["messages"] if m["role"] == "tool"]
    assert '"amount": "450 000 ₽"' in tool_messages[1]["content"]


def test_chat_reports_a_failed_tool_instead_of_crashing(svc):
    forecast(svc)
    client = Scripted(
        [
            {
                "tool_calls": [
                    call("add_payment", direction="in", amount_rub=1, date="2020-01-01", label="x")
                ]
            },
            "Дата в прошлом, платёж не добавлен.",
        ]
    )
    reply = Assistant(svc, client).chat(
        [{"role": "user", "content": "Добавь платёж"}],
        {"account_id": "A", "as_of": "2026-04-30", "model": "weekday"},
        OWNER,
    )
    assert (
        reply["actions"][0]["ok"] is False and "после даты расчёта" in reply["actions"][0]["error"]
    )


def test_ocr_text_document_becomes_payment_drafts(svc):
    fields = {
        "doc_type": "contract",
        "number": "МЗ-44",
        "seller": {"name": "ООО Ромашка", "inn": "0012345678"},
        "buyer": {"name": "ООО Клиент", "inn": "12"},
        "direction": "in",
        "amount_minor": 45_000_000,
        "schedule": [
            {"date": "2026-05-10", "amount_minor": 13_500_000, "label": "аванс 30%"},
            {"date": "2026-06-10", "amount_minor": 31_500_000, "label": "окончательный расчёт"},
        ],
        "confidence": 0.9,
    }
    client = Scripted([fields])
    text = "Договор поставки № МЗ-44 от 01.04.2026. Аванс 30% до 10.05.2026, остаток до 10.06.2026."
    result = Assistant(svc, client).ocr("dogovor.txt", text.encode(), OWNER, "A")
    assert result["engine"] == "tesseract + llm" and result["fields"]["buyer"]["inn"] is None
    drafts = result["drafts"]
    assert [d["amount_minor"] for d in drafts] == [13_500_000, 31_500_000]
    assert drafts[0]["source"] == "contract" and drafts[0]["certainty"] == "agreed"
    assert drafts[0]["label"].startswith("Договор №МЗ-44 · аванс 30%")


def test_client_off_budget_and_json_parsing():
    with pytest.raises(LLMUnavailable, match="выключен"):
        LLMClient(LLMSettings()).chat([{"role": "user", "content": "x"}])
    assert "SECRET" not in repr(LLMSettings(api_key="SECRET"))

    def answer(request):
        assert request.headers["Authorization"] == "Bearer k"
        body = json.loads(request.content)
        assert body["reasoning_effort"] == "low" and body["model"] == "mimo/test"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "ok", "reasoning_content": "r"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"cost": 6.0},
            },
        )

    client = LLMClient(
        LLMSettings(**{**READY.__dict__, "daily_budget_usd": 5}), httpx.MockTransport(answer)
    )
    assert client.chat([{"role": "user", "content": "x"}])["content"] == "ok"
    with pytest.raises(LLMUnavailable, match="бюджет"):
        client.chat([{"role": "user", "content": "x"}])
    assert client.status()["spent_today_usd"] == 6.0

    # A provider without cost in usage (MiMo token plan) is limited by tokens.
    def tokens(request):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 700},
            },
        )

    plan = LLMClient(
        LLMSettings(**{**READY.__dict__, "daily_token_limit": 1000}), httpx.MockTransport(tokens)
    )
    plan.chat([{"role": "user", "content": "x"}])
    plan.chat([{"role": "user", "content": "x"}])
    with pytest.raises(LLMUnavailable, match="бюджет"):
        plan.chat([{"role": "user", "content": "x"}])
    assert plan.status()["tokens_today"] == 1400
    assert parse_json('Ответ: ```json\n{"a": "}{", "b": [1]}\n``` конец') == {"a": "}{", "b": [1]}


def test_api_reports_ai_off_as_503(svc):
    from fastapi.testclient import TestClient

    from apps.api.main import create_app

    run = forecast(svc)
    api = TestClient(
        create_app(
            svc,
            users={"t": OWNER},
            auth_mode="token",
            assistant=Assistant(svc, LLMClient(LLMSettings())),
        )
    )
    auth = {"Authorization": "Bearer t"}
    assert api.get("/api/v1/ai/status", headers=auth).json()["enabled"] is False
    assert api.get(f"/api/v1/ai/forecasts/{run.run_id}/review", headers=auth).json() is None
    off = api.post(f"/api/v1/ai/forecasts/{run.run_id}/review", headers=auth)
    assert off.status_code == 503 and "выключен" in off.json()["detail"]


def test_foreign_characters_are_removed_from_answers():
    assert (
        clean_text("Минимум 40 476 ₽, то есть窟窿 не будет.")
        == "Минимум 40 476 ₽, то есть не будет."
    )
    assert clean_text("Обычный ответ без лишнего.") == "Обычный ответ без лишнего."


# ------------------------------------------------------------ MCP connectors and chat jobs


def test_mcp_server_speaks_json_rpc_and_respects_access(svc):
    from cashflow_services.mcp_server import MCPServer

    run = forecast(svc)
    mcp = MCPServer(svc)
    hello = mcp.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        OWNER,
    )
    assert hello["result"]["protocolVersion"] == "2025-06-18"
    assert hello["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, OWNER) is None
    tools = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, OWNER)["result"][
        "tools"
    ]
    names = {t["name"] for t in tools}
    assert {
        "get_forecast",
        "get_transactions",
        "get_payment_calendar",
        "get_model_quality",
    } <= names
    assert all(t["annotations"]["readOnlyHint"] and t["name"].startswith("get_") for t in tools)
    called = mcp.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_forecast",
                "arguments": {"account_id": "A", "run_id": run.run_id},
            },
        },
        OWNER,
    )["result"]
    assert not called["isError"] and called["structuredContent"]["run_id"] == run.run_id
    assert json.loads(called["content"][0]["text"])["as_of"] == "2026-04-30"
    denied = mcp.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "get_transactions", "arguments": {"account_id": "A"}},
        },
        {"id": "bob", "accounts": []},
    )["result"]
    assert denied["isError"] and "доступ" in denied["content"][0]["text"].lower()
    unknown = mcp.handle(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "drop_all"}}, OWNER
    )
    assert unknown["error"]["code"] == -32602


def test_mcp_tools_answer_real_questions(svc):
    from cashflow_services.mcp_server import MCPServer

    forecast(svc)
    mcp = MCPServer(svc)
    paid = mcp.call(
        "get_transactions", {"account_id": "A", "direction": "out", "sort": "amount_desc"}, OWNER
    )
    assert paid["found"] > 0 and all(t["direction"] == "out" for t in paid["items"])
    history = mcp.call("get_balance_history", {"account_id": "A"}, OWNER)
    assert history["period"].endswith("2026-05-28") and history["days"]
    assert mcp.call("get_accounts", {"query": "a"}, OWNER)["accounts"][0]["account_id"] == "A"


def test_mcp_over_http(svc):
    from fastapi.testclient import TestClient

    from apps.api.main import create_app

    api = TestClient(create_app(svc, users={"t": OWNER}, auth_mode="token"))
    auth = {"Authorization": "Bearer t", "Accept": "application/json, text/event-stream"}
    listed = api.post(
        "/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=auth
    )
    assert listed.status_code == 200 and len(listed.json()["result"]["tools"]) >= 15
    note = api.post(
        "/api/v1/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=auth
    )
    assert note.status_code == 202
    assert api.get("/api/v1/mcp", headers=auth).status_code == 405
    assert api.post("/api/v1/mcp", json={}, headers={}).status_code == 403


def test_chat_job_reports_stage_tools_and_thinking(svc):
    import time

    from cashflow_services.ai import ChatJobs

    run = forecast(svc)
    client = Scripted(
        [{"tool_calls": [call("get_payment_calendar", days=7)]}, "На неделе два платежа."]
    )
    jobs = ChatJobs(Assistant(svc, client))
    job_id = jobs.start(
        [{"role": "user", "content": "Что на неделе?"}],
        {"account_id": "A", "as_of": "2026-04-30", "run_id": run.run_id, "model": "weekday"},
        OWNER,
    )
    for _ in range(100):
        state = jobs.get(job_id, OWNER)
        if state["status"] != "running":
            break
        time.sleep(0.05)
    assert state["status"] == "done" and state["result"]["reply"] == "На неделе два платежа."
    assert state["actions"][0]["title"] == "MCP · Платёжный календарь"
    assert state["reasoning"] and state["elapsed"] >= 0
    with pytest.raises(ValueError):
        jobs.get(job_id, {"id": "bob", "accounts": []})
