"""OpenAI-совместимый провайдер (OpenRouter). Перенесено из VM_SM app/llm/client.py:
разбор JSON из ответа, стоимость из usage.cost, отключение reasoning у flash-моделей.

Провайдер делает ОДНУ попытку — повторы и учёт расходов делает AIRouter, чтобы каждая оплаченная попытка
попала в журнал."""
import json
import re
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.core.config import settings

# Запасные цены за 1M токенов (вход, выход), если провайдер не вернул стоимость сам.
# Неизвестная модель считается с запасом.
FALLBACK_PRICES: dict[str, tuple[float, float]] = {
    "qwen/qwen3.8-flash": (0.15, 0.47),
}
UNKNOWN_PRICE = (1.0, 4.0)


class AIError(RuntimeError):
    def __init__(self, message: str, *, usage: dict | None = None, retryable: bool = True, latency_ms: int = 0):
        super().__init__(message)
        self.usage = usage or {}
        self.retryable = retryable
        self.latency_ms = latency_ms


@dataclass
class AIResult:
    data: dict
    model: str
    provider: str
    usage: dict = field(default_factory=dict)
    latency_ms: int = 0

    @property
    def input_tokens(self) -> int | None:
        return self.usage.get("prompt_tokens")

    @property
    def output_tokens(self) -> int | None:
        return self.usage.get("completion_tokens")


def estimate_cost(model: str, usage: dict) -> float:
    if isinstance(usage.get("cost"), int | float):
        return float(usage["cost"])
    price_in, price_out = FALLBACK_PRICES.get(model, UNKNOWN_PRICE)
    return ((usage.get("prompt_tokens") or 0) * price_in + (usage.get("completion_tokens") or 0) * price_out) / 1e6


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"В ответе нет JSON: {text[:200]}")
    return json.loads(text[start:end + 1])


class AIProvider(Protocol):
    name: str

    async def chat_json(self, model: str, system: str, user: str, *, temperature: float,
                        max_tokens: int) -> AIResult: ...


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = settings.openrouter_api_key if api_key is None else api_key
        self.base_url = base_url or settings.openrouter_base_url
        self._transport = transport

    async def chat_json(self, model: str, system: str, user: str, *, temperature: float = 0.2,
                        max_tokens: int = 2000) -> AIResult:
        if not self.api_key:
            raise AIError("Не задан OPENROUTER_API_KEY", retryable=False)
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "usage": {"include": True},
            # Без этого flash-модели тратят max_tokens на скрытое «размышление» и JSON обрезается (проверено в VM_SM)
            "reasoning": {"enabled": False},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "X-Title": settings.app_name}
        started = time.monotonic()
        usage: dict = {}
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_sec, transport=self._transport) as client:
                resp = await client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as e:
            raise AIError(f"сеть: {type(e).__name__}", latency_ms=_ms(started)) from e
        if resp.status_code >= 400:
            retryable = resp.status_code == 429 or resp.status_code >= 500
            raise AIError(f"HTTP {resp.status_code}: {resp.text[:300]}", retryable=retryable, latency_ms=_ms(started))
        try:
            payload = resp.json()
            usage = payload.get("usage") or {}
            content = payload["choices"][0]["message"]["content"] or ""
            data = parse_json(content)
        except (ValueError, KeyError, IndexError) as e:
            # Ответ пришёл, но JSON битый — токены всё равно оплачены
            raise AIError(f"битый ответ модели: {e}", usage=usage, latency_ms=_ms(started)) from e
        return AIResult(data=data, model=model, provider=self.name, usage=usage, latency_ms=_ms(started))


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
