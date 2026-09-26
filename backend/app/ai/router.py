"""AI Router — единственная точка вызова моделей.

Выбирает модель по задаче (analyze / classify / write / qa), проверяет квоту организации до вызова,
повторяет при 429/5xx/битом JSON, валидирует ответ Pydantic-схемой и пишет каждую попытку
в llm_requests и usage_events."""
import asyncio
from typing import Literal

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.openrouter import AIError, AIProvider, AIResult, OpenRouterProvider, estimate_cost
from app.billing import quotas, usage
from app.core.config import settings
from app.models import LLMRequest

Task = Literal["analyze", "classify", "write", "qa"]


def model_for(task: Task) -> str:
    return {
        "analyze": settings.llm_model_analyze,
        "classify": settings.llm_model_classify,
        "write": settings.llm_model_write,
        "qa": settings.llm_model_qa,
    }[task]


class AIRouter:
    def __init__(self, session: AsyncSession, provider: AIProvider | None = None, backoff_sec: float = 2.0):
        self.session = session
        self.provider = provider or OpenRouterProvider()
        self.backoff_sec = backoff_sec

    async def run(self, task: Task, system: str, user: str, *, org_id: int, operation: str,
                  schema: type[BaseModel] | None = None, job_id: int | None = None,
                  temperature: float | None = None, max_tokens: int = 2000) -> dict:
        await quotas.check(self.session, org_id, "ai_cost_usd_month", amount=0)
        model = model_for(task)
        temperature = temperature if temperature is not None else (0.7 if task == "write" else 0.1)
        last_error: Exception | None = None
        for attempt in range(settings.llm_retries):
            try:
                result = await self.provider.chat_json(model, system, user, temperature=temperature,
                                                       max_tokens=max_tokens)
            except AIError as e:
                await self._log(task, operation, model, e.usage, org_id, job_id, e.latency_ms, ok=False, error=str(e))
                last_error = e
                if not e.retryable:
                    break
                await asyncio.sleep(self.backoff_sec * (attempt + 1) + (4 * self.backoff_sec if "429" in str(e) else 0))
                continue
            try:
                data = schema.model_validate(result.data).model_dump() if schema else result.data
            except ValidationError as e:
                await self._log(task, operation, model, result.usage, org_id, job_id, result.latency_ms,
                                ok=False, error=f"схема: {e.error_count()} ошибок", provider=result.provider)
                last_error = e
                continue
            await self._log(task, operation, model, result.usage, org_id, job_id, result.latency_ms, ok=True,
                            provider=result.provider)
            return data
        raise AIError(f"{model}: {last_error}", retryable=False)

    async def _log(self, task: str, operation: str, model: str, raw_usage: dict, org_id: int, job_id: int | None,
                   latency_ms: int, *, ok: bool, error: str | None = None, provider: str | None = None) -> None:
        provider = provider or self.provider.name
        res = AIResult(data={}, model=model, provider=provider, usage=raw_usage)
        cost = estimate_cost(model, raw_usage) if raw_usage else 0.0
        self.session.add(LLMRequest(organization_id=org_id, task=task, operation=operation, provider=provider,
                                    model=model, input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                                    cost_usd=cost, latency_ms=latency_ms, ok=ok, error=error, job_id=job_id))
        if cost:
            await usage.record(self.session, org_id, "ai_cost_usd", operation, cost, provider=provider, model=model,
                               input_tokens=res.input_tokens, output_tokens=res.output_tokens, cost_usd=cost,
                               job_id=job_id, commit=False)
        await self.session.commit()
