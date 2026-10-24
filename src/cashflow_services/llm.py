"""The LLM through an OpenAI-compatible endpoint: forecast review, recommendations, OCR, chat.

The provider is configuration (LLM_BASE_URL, LLM_MODEL_ID); the stand uses Xiaomi MiMo.

The model never produces forecast numbers: it reads them from our tools and explains them.
Logs carry only status, latency, tokens and cost — never prompts, answers or the key.
"""

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date

import httpx

log = logging.getLogger("cashflow.llm")


class LLMUnavailable(RuntimeError):
    """The text is shown to the user as is."""


@dataclass
class LLMSettings:
    enabled: bool = False
    api_key: str = field(default="", repr=False)
    base_url: str = ""
    model_id: str = ""
    timeout_seconds: float = 120
    max_tokens: int = 6000
    # The model thinks before answering; «low» keeps the thinking from eating the answer budget.
    reasoning_effort: str = "low"
    # A runaway chat loop must not burn the key: calls stop at these daily totals. Providers
    # that report no cost (MiMo token plan) are limited by tokens.
    daily_budget_usd: float = 10
    daily_token_limit: int = 3_000_000

    @classmethod
    def from_env(cls):
        def env(name, default=""):
            return os.getenv(f"LLM_{name}") or default

        return cls(
            enabled=os.getenv("LLM_ENABLED", "false").lower() == "true",
            api_key=env("API_KEY"),
            base_url=env("BASE_URL"),
            model_id=env("MODEL_ID"),
            timeout_seconds=float(env("TIMEOUT_SECONDS", "120")),
            max_tokens=int(env("MAX_TOKENS", "6000")),
            reasoning_effort=env("REASONING_EFFORT", "low"),
            daily_budget_usd=float(env("DAILY_BUDGET_USD", "10")),
            daily_token_limit=int(env("DAILY_TOKENS", "3000000")),
        )

    @property
    def ready(self) -> bool:
        return self.enabled and all((self.api_key, self.base_url, self.model_id))


# MiMo sometimes slips a Chinese word into a Russian answer: the prompts forbid it, this removes
# what still gets through (CJK ideographs, kana, hangul, full-width punctuation).
_FOREIGN = re.compile(
    r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uff00-\uffef]+"
)


def clean_text(text: str) -> str:
    if not _FOREIGN.search(text or ""):
        return text
    return re.sub(r"[ \t]{2,}", " ", _FOREIGN.sub(" ", text)).replace(" ,", ",").replace(" .", ".")


def parse_json(text: str) -> dict:
    """The first JSON object in a reply, tolerating ```json fences and text around it."""
    text = re.sub(r"```(?:json)?", "", text or "")
    start = text.find("{")
    if start < 0:
        raise ValueError("В ответе модели нет JSON")
    depth, quoted, escaped = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if quoted:
            escaped = ch == "\\" and not escaped
            if ch == '"' and not escaped:
                quoted = False
            continue
        if ch == '"':
            quoted = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("JSON в ответе модели оборван")


class LLMClient:
    def __init__(self, settings=None, transport=None):
        self.settings = settings or LLMSettings.from_env()
        self._transport = transport
        self._lock = threading.Lock()
        self._day = date.today()
        self._spent = 0.0
        self._tokens = 0
        self._calls = 0

    def status(self) -> dict:
        s = self.settings
        with self._lock:
            self._roll()
            return {
                "enabled": s.ready,
                "model": s.model_id if s.ready else None,
                "calls_today": self._calls,
                "tokens_today": self._tokens,
                "daily_token_limit": s.daily_token_limit,
                "spent_today_usd": round(self._spent, 4),
                "daily_budget_usd": s.daily_budget_usd,
            }

    def _roll(self):
        if self._day != date.today():
            self._day, self._spent, self._tokens, self._calls = date.today(), 0.0, 0, 0

    def chat(self, messages, *, tools=None, max_tokens=None, temperature=0.3) -> dict:
        s = self.settings
        if not s.enabled:
            raise LLMUnavailable("ИИ выключен на этом сервере (LLM_ENABLED=false)")
        if not s.ready:
            raise LLMUnavailable("ИИ не настроен: нет ключа, адреса или модели")
        with self._lock:
            self._roll()
            if self._spent >= s.daily_budget_usd or self._tokens >= s.daily_token_limit:
                raise LLMUnavailable("Дневной бюджет ИИ исчерпан, попробуйте завтра")
        body = {
            "model": s.model_id,
            "messages": messages,
            "max_tokens": max_tokens or s.max_tokens,
            "temperature": temperature,
        }
        if s.reasoning_effort:
            body["reasoning_effort"] = s.reasoning_effort
        if tools:
            body["tools"] = tools
        started = time.monotonic()
        try:
            with httpx.Client(timeout=s.timeout_seconds, transport=self._transport) as http:
                response = http.post(
                    s.base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {s.api_key}"},
                    json=body,
                )
        except httpx.HTTPError as exc:
            log.warning("llm error=%s", type(exc).__name__)
            raise LLMUnavailable("ИИ не ответил вовремя, попробуйте ещё раз") from exc
        elapsed = time.monotonic() - started
        if response.status_code != 200:
            log.warning("llm status=%s elapsed=%.1fs", response.status_code, elapsed)
            raise LLMUnavailable(f"ИИ временно недоступен (код {response.status_code})")
        data = response.json()
        usage = data.get("usage") or {}
        cost = float(usage.get("cost") or 0)
        with self._lock:
            self._spent += cost
            self._tokens += int(usage.get("total_tokens") or 0)
            self._calls += 1
        log.info(
            "llm ok elapsed=%.1fs prompt=%s completion=%s cost=%.4f",
            elapsed,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            cost,
        )
        choice = data["choices"][0]
        message = choice["message"]
        return {
            "finish_reason": choice.get("finish_reason"),
            "content": clean_text((message.get("content") or "").strip()),
            "reasoning": (message.get("reasoning_content") or "").strip(),
            "tool_calls": message.get("tool_calls") or [],
            "usage": usage,
            "elapsed": round(elapsed, 1),
        }

    def json(self, messages, *, max_tokens=None) -> tuple[dict, dict]:
        """A JSON object from the model and the raw reply; one retry if the JSON is broken."""
        reply = self.chat(messages, max_tokens=max_tokens, temperature=0.2)
        try:
            return parse_json(reply["content"]), reply
        except (ValueError, json.JSONDecodeError):
            retry = [
                *messages,
                {"role": "assistant", "content": reply["content"][:4000]},
                {"role": "user", "content": "Верни только корректный JSON по схеме, без текста."},
            ]
            second = self.chat(retry, max_tokens=max_tokens, temperature=0)
            try:
                return parse_json(second["content"]), second
            except (ValueError, json.JSONDecodeError) as exc:
                raise LLMUnavailable("ИИ вернул ответ не в том формате") from exc
