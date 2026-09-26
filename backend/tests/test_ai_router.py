import json

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.ai.openrouter import AIError, AIResult, OpenRouterProvider, estimate_cost, parse_json
from app.ai.router import AIRouter
from app.billing import usage
from app.billing.quotas import QuotaExceeded
from app.models import LLMRequest, UsageEvent
from tests.conftest import register


class FakeProvider:
    name = "fake"

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def chat_json(self, model, system, user, *, temperature, max_tokens):
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return AIResult(data=out, model=model, provider=self.name,
                        usage={"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.001})


class Topic(BaseModel):
    topic: str
    score: int


async def _rows(session, model, org_id):
    return list((await session.execute(select(model).where(model.organization_id == org_id))).scalars())


async def test_success_logs_request_and_usage(client, session):
    acc = await register(client)
    provider = FakeProvider([{"topic": "AI в продажах", "score": 7}])
    data = await AIRouter(session, provider, backoff_sec=0).run(
        "classify", "sys", "user", org_id=acc.org_id, operation="classify_post", schema=Topic)
    assert data == {"topic": "AI в продажах", "score": 7}
    reqs = await _rows(session, LLMRequest, acc.org_id)
    assert len(reqs) == 1 and reqs[0].ok and reqs[0].model == "qwen/qwen3.8-flash"
    events = await _rows(session, UsageEvent, acc.org_id)
    assert [(e.metric, float(e.quantity)) for e in events] == [("ai_cost_usd", 0.001)]


async def test_retry_after_broken_json_and_bad_schema(client, session):
    acc = await register(client)
    provider = FakeProvider([
        AIError("битый ответ модели", usage={"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0005}),
        {"topic": "x"},  # нет score — не проходит схему
        {"topic": "x", "score": 3},
    ])
    data = await AIRouter(session, provider, backoff_sec=0).run(
        "analyze", "s", "u", org_id=acc.org_id, operation="audit", schema=Topic)
    assert data["score"] == 3 and provider.calls == 3
    reqs = await _rows(session, LLMRequest, acc.org_id)
    assert [r.ok for r in reqs] == [False, False, True]
    # оплаченные неудачные попытки тоже в расходе
    total = sum(float(e.quantity) for e in await _rows(session, UsageEvent, acc.org_id))
    assert total == pytest.approx(0.0025)


async def test_non_retryable_error_stops(client, session):
    acc = await register(client)
    provider = FakeProvider([AIError("HTTP 400", retryable=False), {"ok": 1}])
    with pytest.raises(AIError):
        await AIRouter(session, provider, backoff_sec=0).run("write", "s", "u", org_id=acc.org_id, operation="gen")
    assert provider.calls == 1


async def test_quota_blocks_before_call(client, session):
    acc = await register(client)  # free: ai_cost_usd_month = 0.5
    await usage.record(session, acc.org_id, "ai_cost_usd", "earlier", 0.5)
    provider = FakeProvider([{"ok": 1}])
    with pytest.raises(QuotaExceeded):
        await AIRouter(session, provider, backoff_sec=0).run("write", "s", "u", org_id=acc.org_id, operation="gen")
    assert provider.calls == 0


def test_parse_json_variants():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Вот ответ: {"a": {"b": 2}} спасибо') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        parse_json("нет json")


def test_estimate_cost_prefers_provider_cost():
    assert estimate_cost("any", {"cost": 0.02, "prompt_tokens": 10**6}) == 0.02
    assert estimate_cost("qwen/qwen3.8-flash", {"prompt_tokens": 10**6, "completion_tokens": 10**6}) == \
        pytest.approx(0.62)


async def test_openrouter_provider_request_and_errors():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        if seen["model"] == "bad":
            return httpx.Response(400, text="bad model")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '```json\n{"x": 1}\n```'}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "cost": 0.0001}})

    provider = OpenRouterProvider(api_key="k", transport=httpx.MockTransport(handler))
    res = await provider.chat_json("qwen/qwen3.8-flash", "s", "u", temperature=0.1, max_tokens=100)
    assert res.data == {"x": 1} and res.usage["cost"] == 0.0001
    assert seen["reasoning"] == {"enabled": False} and seen["response_format"] == {"type": "json_object"}
    with pytest.raises(AIError) as e:
        await provider.chat_json("bad", "s", "u", temperature=0.1, max_tokens=100)
    assert e.value.retryable is False


async def test_provider_without_key():
    with pytest.raises(AIError) as e:
        await OpenRouterProvider(api_key="").chat_json("m", "s", "u", temperature=0, max_tokens=1)
    assert not e.value.retryable
