import hashlib
import json
import math
import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.ai.embeddings import EmbeddingError, EmbedResult
from app.ai.openrouter import AIResult
from app.ai.router import AIRouter
from app.analysis import classify, dedupe, metrics, pipeline, taxonomy
from app.billing import usage
from app.core.config import settings
from app.models import (
    GlobalPost,
    GlobalSource,
    LLMRequest,
    PostAnalysis,
    PostEmbedding,
    Source,
    SourceKind,
    SourceStatus,
    UsageEvent,
)
from app.workers.tasks import run_job, sync_source
from tests.conftest import register, set_plan
from tests.test_roles import _join
from tests.test_sources import handle, web  # noqa: F401 — фикстура «интернета» с фикстурами VM_SM

NOW = datetime.now(UTC)


class FakeClassifier:
    """Модель-классификатор: размечает каждый пост из входа; можно ломать отдельные ответы."""
    name = "fake"

    def __init__(self, topic: str = "кейсы и результаты", skip: int = 0, fail: Exception | None = None):
        self.topic, self.skip, self.fail = topic, skip, fail
        self.calls: list[dict] = []

    async def chat_json(self, model, system, user, *, temperature, max_tokens):
        self.calls.append({"model": model, "system": system, "user": user})
        if self.fail:
            raise self.fail
        items = [{"id": p["id"], "content_type": "кейс", "funnel_stage": "захват лида", "hook_type": "ЦИФРА",
                  "cta_type": "что-то новое", "proof_type": "цифры", "tone": "экспертный", "value_type": "обучение",
                  "topic": self.topic, "target_role": "специалист", "has_case": True, "has_numbers": "да",
                  "has_offer": False, "has_lead_magnet": "false", "summary": f"пост {p['id']}"}
                 for p in json.loads(user)][self.skip:]
        return AIResult(data={"items": items}, model=model, provider=self.name,
                        usage={"prompt_tokens": 1000, "completion_tokens": 400, "cost": 0.002})


def vector(text: str) -> list[float]:
    rnd = random.Random(hashlib.sha256(text.encode()).digest())
    v = [rnd.uniform(-1, 1) for _ in range(settings.embedding_dim)]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


class FakeEmbedder:
    name, model, dim = "fake", "fake-embed", settings.embedding_dim

    def __init__(self, fail: bool = False):
        self.fail, self.calls = fail, 0

    async def embed(self, texts):
        self.calls += 1
        if self.fail:
            raise EmbeddingError("HTTP 404: embeddings not supported")
        return EmbedResult(vectors=[vector(t) for t in texts], model=self.model,
                           usage={"prompt_tokens": 10 * len(texts), "cost": 0.0001})


def handler(provider=None, embedder=None):
    async def run(session, job):
        router = AIRouter(session, provider or FakeClassifier(), backoff_sec=0)
        return await pipeline.handle_analyze_source(session, job, router=router, embedder=embedder or FakeEmbedder())
    return run


async def make_source(session, org_id: int, posts: list[dict], kind=SourceKind.telegram) -> Source:
    gs = GlobalSource(kind=kind, key=f"k_{uuid.uuid4().hex[:10]}", url="https://example.com", status=SourceStatus.ok,
                      meta={})
    session.add(gs)
    await session.flush()
    for i, p in enumerate(posts):
        text = p.get("text", f"Текст поста номер {i} про работу с клиентами {uuid.uuid4().hex}")
        session.add(GlobalPost(global_source_id=gs.id, external_id=str(i), text=text,
                               published_at=p.get("published_at", NOW - timedelta(days=i)),
                               content_hash=hashlib.sha256(text.lower().encode()).hexdigest(),
                               canonical_url=p.get("canonical_url"),
                               **{k: p[k] for k in ("views", "likes", "comments", "shares") if k in p}))
    src = Source(organization_id=org_id, global_source_id=gs.id)
    session.add(src)
    await session.commit()
    return src


async def analyze(acc, session, source_id: int, **kw) -> dict:
    job = await pipeline.start(session, None, acc.org_id, source_id)
    assert job is not None
    await run_job(job.id, handler(**kw))
    done = (await acc.get(f"/api/v1/jobs/{job.id}")).json()
    assert done["status"] == "completed", done
    return done["result"]


# --- чистые функции ---

def test_normalize_maps_variants_and_falls_back():
    tax = taxonomy.OrgTaxonomy(topics=["цены", "другое"], roles=["собственник", "широкая аудитория"], version=1,
                               niche="кофейни")
    out = classify.normalize({"content_type": "Кейс", "funnel_stage": "захват лида", "hook_type": "непонятно",
                              "topic": "ЦЕНЫ", "target_role": "маркетолог", "has_case": "да", "has_numbers": 0,
                              "summary": "x" * 500}, tax)
    assert out["content_type"] == "кейс" and out["funnel_stage"] == "захват_лида"
    assert out["hook_type"] == "нет" and out["topic"] == "цены" and out["target_role"] == "широкая аудитория"
    assert out["has_case"] is True and out["has_numbers"] is False and len(out["summary"]) == 300
    assert out["tone"] == "дружелюбный"  # нет в ответе — запасное значение


def test_clean_labels_and_system_prompt():
    assert taxonomy.clean_labels([" Цены  и акции.", "цены и акции", "", "Другое"], "другое") == \
        ["цены и акции", "другое"]
    tax = taxonomy.OrgTaxonomy(topics=["цены", "другое"], roles=["широкая аудитория"], version=1, niche="кофейни")
    prompt = classify.system_prompt(tax)
    assert "кофейни" in prompt and "цены | другое" in prompt and "{fields}" not in prompt


# --- метрики и дубли ---

async def test_metrics_er_and_overperformance(client, session):
    acc = await register(client)
    src = await make_source(session, acc.org_id, [
        {"views": 100, "likes": 5, "comments": 1}, {"views": 200, "likes": 4}, {"views": 300},
        {"views": 1000, "likes": 50, "shares": 10}, {"likes": 7},
    ])
    gs = await session.get(GlobalSource, src.global_source_id)
    res = await metrics.calculate(session, gs)
    assert res["median_views"] == 250 and res["with_overperformance"] == 5
    posts = {p.external_id: p for p in (await session.execute(
        select(GlobalPost).where(GlobalPost.global_source_id == gs.id))).scalars()}
    assert posts["0"].engagement == 6 and posts["0"].er == 6.0
    assert posts["3"].overperformance == 4.0 and posts["3"].er == 6.0
    assert posts["2"].engagement is None and posts["2"].er is None
    # нет просмотров — сравнивается с медианой вовлечённости источника (6, 4, 60, 7 → 6.5)
    assert posts["4"].overperformance == round(7 / 6.5, 3)


async def test_metrics_without_views_use_engagement(client, session):
    acc = await register(client)
    src = await make_source(session, acc.org_id, [{"likes": 10}, {"likes": 20}, {"likes": 30}, {"likes": 80}],
                            kind=SourceKind.instagram)
    gs = await session.get(GlobalSource, src.global_source_id)
    await metrics.calculate(session, gs)
    top = (await session.execute(select(GlobalPost).where(GlobalPost.global_source_id == gs.id,
                                                          GlobalPost.external_id == "3"))).scalar_one()
    assert gs.median_views is None and gs.median_engagement == 25 and top.overperformance == 3.2


async def test_exact_duplicates_across_sources(client, session):
    acc = await register(client)
    long_text = "Как мы сократили время ответа клиентам в три раза: разбор процесса, цифры и ошибки. " * 2
    first = await make_source(session, acc.org_id, [{"text": long_text}, {"text": "Короткий общий"}])
    second = await make_source(session, acc.org_id, [
        {"text": long_text.upper()}, {"text": "Короткий общий"},
        {"text": "Другая статья", "canonical_url": "https://blog.example.com/a"}])
    await make_source(session, acc.org_id,
                      [{"text": "Та же статья в RSS", "canonical_url": "https://blog.example.com/a"}])
    gs2 = await session.get(GlobalSource, second.global_source_id)
    assert await dedupe.exact(session, gs2) == 1  # короткий текст не считается дублем по хэшу
    dup = (await session.execute(select(GlobalPost).where(GlobalPost.global_source_id == gs2.id,
                                                          GlobalPost.external_id == "0"))).scalar_one()
    orig = (await session.execute(select(GlobalPost).where(GlobalPost.global_source_id == first.global_source_id,
                                                           GlobalPost.external_id == "0"))).scalar_one()
    assert dup.duplicate_of_id == orig.id


# --- конвейер ---

async def test_sync_then_analyze_classifies_embeds_and_scores(client, session, web):  # noqa: F811
    acc = await register(client)
    src = (await acc.post("/api/v1/sources", json={"url": f"@{handle()}"})).json()
    await sync_source({}, src["last_job"]["id"])

    queued = (await acc.get(f"/api/v1/sources/{src['id']}")).json()["last_analysis"]
    assert queued["status"] == "queued"  # анализ поставлен автоматически после сбора
    provider, embedder = FakeClassifier(), FakeEmbedder()
    await run_job(queued["id"], handler(provider, embedder))

    done = (await acc.get(f"/api/v1/sources/{src['id']}")).json()
    job = done["last_analysis"]
    assert job["status"] == "completed", job
    r = job["result"]
    assert r["classified"] + r["no_text"] == 9 and r["classify_errors"] == 0 and r["embedded"] == r["classified"]
    assert r["metrics"]["median_views"] and done["analyzed_count"] == r["classified"]
    assert len(provider.calls) == math.ceil(r["classified"] / classify.BATCH)
    assert provider.calls[0]["model"] == "qwen/qwen3.8-flash"

    posts = (await acc.get(f"/api/v1/sources/{src['id']}/posts")).json()
    labelled = [p for p in posts if p["analysis"] and not p["analysis"]["error"]]
    p = labelled[0]
    assert p["analysis"]["funnel_stage"] == "захват_лида" and p["analysis"]["hook_type"] == "цифра"
    assert p["analysis"]["cta_type"] == "нет" and p["analysis"]["topic"] == "кейсы и результаты"
    assert p["analysis"]["has_numbers"] is True and p["has_embedding"]
    assert all(q["overperformance"] is not None for q in posts if q["views"])

    llm = (await session.execute(select(func.count()).select_from(LLMRequest).where(
        LLMRequest.organization_id == acc.org_id, LLMRequest.job_id == queued["id"]))).scalar_one()
    assert llm == len(provider.calls) + embedder.calls
    spent = (await session.execute(select(func.sum(UsageEvent.quantity)).where(
        UsageEvent.organization_id == acc.org_id, UsageEvent.metric == "ai_cost_usd"))).scalar_one()
    assert float(spent) == pytest.approx(0.002 * len(provider.calls) + 0.0001 * embedder.calls)

    again = await analyze(acc, session, src["id"])  # повтор ничего не переразмечает и не платит
    assert again["classified"] == 0 and again["embedded"] == 0


async def test_partial_answer_and_model_failure_are_retried_later(client, session):
    acc = await register(client)
    src = await make_source(session, acc.org_id, [{"views": 10 * i} for i in range(1, 5)])
    r = await analyze(acc, session, src.id, provider=FakeClassifier(skip=1))
    assert r["classified"] == 3 and r["classify_errors"] == 1
    r = await analyze(acc, session, src.id, provider=FakeClassifier())
    assert r["classified"] == 1  # доразметился только пропущенный

    from app.ai.openrouter import AIError
    src2 = await make_source(session, acc.org_id, [{}, {}])
    r = await analyze(acc, session, src2.id, provider=FakeClassifier(fail=AIError("HTTP 500", retryable=True)))
    assert r["classified"] == 0 and r["classify_errors"] == 2
    errors = (await session.execute(select(PostAnalysis.error).join(GlobalPost).where(
        GlobalPost.global_source_id == src2.global_source_id))).scalars().all()
    assert all(e.startswith("ошибка модели") for e in errors)


async def test_taxonomy_change_reclassifies(client, session):
    acc = await register(client)
    src = await make_source(session, acc.org_id, [{}, {}, {}])
    await analyze(acc, session, src.id)
    r = await acc.put("/api/v1/taxonomy", json={"topics": ["Зерно", "обжарка", "Бариста"],
                                                "roles": ["владелец кофейни", "бариста"], "niche": "кофейни"})
    assert r.status_code == 200 and r.json()["version"] == 1 and r.json()["topics"] == ["зерно", "обжарка", "бариста"]
    provider = FakeClassifier(topic="обжарка")
    res = await analyze(acc, session, src.id, provider=provider)
    assert res["classified"] == 3 and res["taxonomy_version"] == 1
    system = provider.calls[0]["system"]
    assert "кофейни" in system and "зерно | обжарка | бариста | другое" in system
    topics = (await session.execute(select(PostAnalysis.topic).join(GlobalPost).where(
        GlobalPost.global_source_id == src.global_source_id))).scalars().all()
    assert set(topics) == {"обжарка"}


async def test_shared_channel_analyzed_per_org_with_own_taxonomy(client, session):
    a = await register(client, "A")
    b = await register(client, "B")
    src_a = await make_source(session, a.org_id, [{}, {}])
    src_b = Source(organization_id=b.org_id, global_source_id=src_a.global_source_id)
    session.add(src_b)
    await session.commit()
    await b.put("/api/v1/taxonomy",
                json={"topics": ["ипотека", "ремонт", "дизайн"], "roles": ["покупатель", "риелтор"]})

    await analyze(a, session, src_a.id, provider=FakeClassifier(topic="кейсы и результаты"))
    rb = await analyze(b, session, src_b.id, provider=FakeClassifier(topic="ремонт"), embedder=FakeEmbedder())
    assert rb["classified"] == 2 and rb["embedded"] == 0  # эмбеддинги уже посчитаны для A
    topics_a = {p["analysis"]["topic"] for p in (await a.get(f"/api/v1/sources/{src_a.id}/posts")).json()}
    topics_b = {p["analysis"]["topic"] for p in (await b.get(f"/api/v1/sources/{src_b.id}/posts")).json()}
    assert topics_a == {"кейсы и результаты"} and topics_b == {"ремонт"}


async def test_semantic_duplicate_found_by_embedding(client, session):
    acc = await register(client)
    same = "Скидка 20% на обжарку до пятницы"  # короче 80 символов — хэш не проверяется, ловит эмбеддинг
    first = await make_source(session, acc.org_id, [{"text": same}])
    second = await make_source(session, acc.org_id, [{"text": same, "published_at": NOW + timedelta(days=1)}])
    await analyze(acc, session, first.id)
    r = await analyze(acc, session, second.id)
    assert r["duplicates"] == 1


async def test_without_key_only_metrics(client, session, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    acc = await register(client)
    src = await make_source(session, acc.org_id, [{"views": 1}, {"views": 2}, {"views": 3}])
    job = await pipeline.start(session, None, acc.org_id, src.id)
    await run_job(job.id, pipeline.handle_analyze_source)
    r = (await acc.get(f"/api/v1/jobs/{job.id}")).json()["result"]
    assert "OPENROUTER_API_KEY" in r["message"] and r["metrics"]["median_views"] == 2
    assert "classified" not in r


async def test_quota_stops_classification_and_embedding_error_is_reported(client, session):
    acc = await register(client)
    await set_plan(acc.org_id, "free")  # Free: $0.5 AI в месяц
    src = await make_source(session, acc.org_id, [{}])
    await usage.record(session, acc.org_id, "ai_cost_usd", "test", 0.5)
    r = await analyze(acc, session, src.id)
    assert "Лимит тарифа" in r["classify_stopped"] and r["classified"] == 0

    other = await register(client)
    src2 = await make_source(session, other.org_id, [{}])
    r = await analyze(other, session, src2.id, embedder=FakeEmbedder(fail=True))
    assert r["classified"] == 1 and "embeddings not supported" in r["embed_error"]
    assert (await session.execute(select(func.count()).select_from(PostEmbedding).join(GlobalPost).where(
        GlobalPost.global_source_id == src2.global_source_id))).scalar_one() == 0


# --- API таксономии ---

async def test_taxonomy_api_defaults_roles_and_validation(client, monkeypatch):
    owner = await register(client)
    t = (await owner.get("/api/v1/taxonomy")).json()
    assert t["is_default"] and t["version"] == 0 and "funnel_stage" in t["universal"]
    assert (await owner.put("/api/v1/taxonomy", json={"topics": ["a", "другое", "A"], "roles": ["x", "y"]})
            ).status_code == 422

    await set_plan(owner.org_id, "starter")
    member = await _join(client, owner, "member")
    assert (await member.get("/api/v1/taxonomy")).status_code == 200
    assert (await member.put("/api/v1/taxonomy", json={"topics": ["a", "b", "c"], "roles": ["x", "y"]})
            ).status_code == 403

    monkeypatch.setattr(settings, "openrouter_api_key", "")
    assert (await owner.post("/api/v1/taxonomy/suggest")).status_code == 503


async def test_taxonomy_suggest_uses_sources_and_posts(client, session):
    acc = await register(client, "Кофейня Зерно")
    await make_source(session, acc.org_id, [{"text": "Новая обжарка эфиопии уже в зале"}])

    class Suggester:
        name = "fake"
        user = ""

        async def chat_json(self, model, system, user, *, temperature, max_tokens):
            Suggester.user = user
            return AIResult(data={"niche": "кофейни Минска", "topics": ["Обжарка", "зерно", "обжарка", "десерты"],
                                  "roles": ["гости", "бариста"], "rationale": "по постам"},
                            model=model, provider="fake", usage={"cost": 0.01})

    out = await taxonomy.suggest(session, acc.org_id, AIRouter(session, Suggester(), backoff_sec=0))
    assert out["topics"] == ["обжарка", "зерно", "десерты"] and out["based_on"] == {"sources": 1, "posts": 1}
    assert "Кофейня Зерно" in Suggester.user and "эфиопии" in Suggester.user
    assert (await acc.get("/api/v1/taxonomy")).json()["is_default"]  # подсказка не сохраняется


async def test_manual_analyze_endpoint_and_isolation(client, session):
    a = await register(client, "A")
    b = await register(client, "B")
    src = await make_source(session, a.org_id, [{}])
    assert (await b.post(f"/api/v1/sources/{src.id}/analyze")).status_code == 404
    r = await a.post(f"/api/v1/sources/{src.id}/analyze")
    assert r.status_code == 202 and r.json()["last_analysis"]["status"] == "queued"
    assert (await a.post(f"/api/v1/sources/{src.id}/analyze")).status_code == 409

    await a.put("/api/v1/taxonomy", json={"topics": ["a1", "a2", "a3"], "roles": ["r1", "r2"]})
    assert (await b.get("/api/v1/taxonomy")).json()["is_default"]


async def test_market_overview(client, session):
    a = await register(client, "A")
    b = await register(client, "B")
    own = await make_source(session, a.org_id, [{"views": 100, "likes": 10}, {"views": 100, "likes": 2},
                                                 {"views": 100}])
    comp = await make_source(session, a.org_id, [{"views": 100}, {"views": 100}, {"views": 900, "likes": 9},
                                                  {"views": 100, "published_at": NOW - timedelta(days=60)}])
    from app.models import SourceRole
    own.role, comp.role = SourceRole.own, SourceRole.competitor
    await session.commit()
    await analyze(a, session, own.id, provider=FakeClassifier(topic="экспертиза и советы"))
    await analyze(a, session, comp.id)

    o = (await a.get("/api/v1/market/overview?days=30")).json()
    assert o["by_role"]["own"]["posts"] == 3 and o["by_role"]["own"]["analyzed"] == 3
    assert o["by_role"]["competitor"]["posts"] == 3  # пост 60-дневной давности вне окна
    assert o["by_role"]["own"]["median_er"] == 6.0  # ER 10% и 2%; пост без реакций не участвует
    topics = {t["topic"]: t for t in o["topics"]}
    assert topics["экспертиза и советы"]["own"] == 3 and topics["кейсы и результаты"]["competitor"] == 3
    assert o["top_posts"][0]["overperformance"] == 9.0 and o["top_posts"][0]["role"] == "competitor"
    assert all(p["role"] != "own" for p in o["top_posts"])

    empty = (await b.get("/api/v1/market/overview")).json()
    assert empty["topics"] == [] and empty["top_posts"] == [] and empty["by_role"]["own"]["posts"] == 0
