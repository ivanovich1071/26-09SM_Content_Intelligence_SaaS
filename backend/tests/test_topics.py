import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import func, select

from app.ai.openrouter import AIError, AIResult
from app.ai.router import AIRouter
from app.core.config import settings
from app.models import (
    Competitor,
    GlobalPost,
    GlobalSource,
    Job,
    PostAnalysis,
    PostEmbedding,
    Source,
    SourceKind,
    SourceRole,
    SourceStatus,
    TopicCluster,
)
from app.topics import cluster, stats
from app.workers.tasks import run_job
from tests.conftest import register, set_plan
from tests.test_roles import _join

NOW = datetime.now(UTC)
DIM = settings.embedding_dim
RNG = np.random.default_rng(7)
CENTRES = RNG.normal(size=(3, DIM))


def near(centre: int) -> list[float]:
    v = CENTRES[centre] + 0.05 * RNG.normal(size=DIM)
    return list(v / np.linalg.norm(v))


async def add_posts(session, org_id: int, *, role: SourceRole, topic: str, n: int, competitor_id: int | None = None,
                    days_ago: float = 1, views: int = 100, groups: int | None = None, texts: list[str] | None = None):
    """Размеченные посты с эмбеддингами — без сети и модели. groups=3 → векторы из трёх смысловых групп."""
    gs = GlobalSource(kind=SourceKind.telegram, key=f"t_{uuid.uuid4().hex[:10]}", url="https://t.me/x",
                      status=SourceStatus.ok, meta={}, title=f"канал {role.value}")
    session.add(gs)
    await session.flush()
    src = Source(organization_id=org_id, global_source_id=gs.id, role=role, competitor_id=competitor_id)
    session.add(src)
    for i in range(n):
        text = texts[i % len(texts)] if texts else f"{topic}: пост {i} {uuid.uuid4().hex[:6]}"
        p = GlobalPost(global_source_id=gs.id, external_id=str(i), text=text,
                       published_at=NOW - timedelta(days=days_ago, minutes=i),
                       content_hash=hashlib.sha256(f"{text}{uuid.uuid4()}".encode()).hexdigest(),
                       views=views * (i + 1), likes=i + 1, engagement=i + 1,
                       er=round(100 * (i + 1) / (views * (i + 1)), 3),
                       overperformance=round((i + 1) / ((n + 1) / 2), 3), media_type="photo" if i % 2 else "text")
        session.add(p)
        await session.flush()
        session.add(PostAnalysis(organization_id=org_id, post_id=p.id, taxonomy_version=0, topic=topic,
                                 content_type="кейс", funnel_stage="охват", hook_type="цифра", cta_type="нет"))
        session.add(PostEmbedding(post_id=p.id, model="fake",
                                  embedding=near(i % groups) if groups else near(sum(map(ord, topic)) % 3)))
    await session.commit()
    return src


async def market_world(client, session):
    acc = await register(client, f"Кофейня-{uuid.uuid4().hex[:4]}")
    await set_plan(acc.org_id, "starter")
    comp = Competitor(organization_id=acc.org_id, name="Кофе Лаб")
    session.add(comp)
    await session.commit()
    await add_posts(session, acc.org_id, role=SourceRole.own, topic="продукт и услуги", n=4)
    await add_posts(session, acc.org_id, role=SourceRole.competitor, topic="кейсы и результаты", n=6,
                    competitor_id=comp.id)
    await add_posts(session, acc.org_id, role=SourceRole.competitor, topic="продукт и услуги", n=2,
                    competitor_id=comp.id)
    await add_posts(session, acc.org_id, role=SourceRole.market, topic="кейсы и результаты", n=4)
    # предыдущий период: кейсы тогда почти не обсуждали
    await add_posts(session, acc.org_id, role=SourceRole.market, topic="продукт и услуги", n=5, days_ago=40)
    await add_posts(session, acc.org_id, role=SourceRole.market, topic="кейсы и результаты", n=1, days_ago=40)
    return acc, comp


async def test_topics_shares_gap_and_trend(client, session):
    acc, _ = await market_world(client, session)
    data = (await acc.get("/api/v1/topics?days=30")).json()
    assert data["own_total"] == 4 and data["market_total"] == 12 and data["prev_market_total"] == 6
    t = {x["topic"]: x for x in data["topics"]}
    cases, product = t["кейсы и результаты"], t["продукт и услуги"]
    assert (cases["competitor"], cases["market"], cases["own"]) == (6, 4, 0)
    assert cases["share_market"] == 83.3 and cases["share_own"] == 0.0 and cases["gap"] == 83.3 and cases["is_gap"]
    assert cases["trend_pp"] == round(83.3 - 16.7, 1) and cases["competitors"] == ["Кофе Лаб"]
    assert cases["saturation_per_week"] == round(10 / (30 / 7), 1)
    assert product["gap"] == round(16.7 - 100, 1) and not product["is_gap"]
    assert data["cluster_job"] is None

    gaps = (await acc.get("/api/v1/topics/gaps?days=30")).json()
    assert [g["topic"] for g in gaps["gaps"]] == ["кейсы и результаты"] and gaps["gaps"][0]["insight"] is None


def test_keywords_are_distinctive_and_deterministic():
    group = ["обжарка эфиопия светлая", "обжарка эфиопия ягоды"]
    rest = ["обжарка кения средняя", "обжарка бразилия тёмная"]
    assert cluster.keywords(group, 3, rest) == ["эфиопия", "светлая", "ягоды"]  # «обжарка» есть везде — не отличает
    assert cluster.keywords(group, 3, rest) == cluster.keywords(list(reversed(group)), 3, rest)


def test_gap_needs_market_volume():
    class R:
        def __init__(self, role, topic):
            self.Source = type("S", (), {"role": role, "competitor_id": None})()
            self.PostAnalysis = type("A", (), {"topic": topic})()
            self.GlobalPost = type("P", (), {"engagement": None, "er": None})()
    rows = [R(SourceRole.market, "редкая"), R(SourceRole.market, "частая"), R(SourceRole.market, "частая"),
            R(SourceRole.market, "частая"), R(SourceRole.own, "своя")]
    t = {x["topic"]: x for x in stats.aggregate(rows, 30)["topics"]}
    assert t["редкая"]["gap"] == 25.0 and not t["редкая"]["is_gap"]  # 1 пост рынка — шум, а не пробел
    assert t["частая"]["is_gap"] and t["своя"]["gap"] == -100.0


async def test_topic_detail(client, session):
    acc, _ = await market_world(client, session)
    d = (await acc.get("/api/v1/topics/detail", params={"topic": "кейсы и результаты", "days": 30})).json()
    assert d["market_total"] == 10 and d["by_competitor"] == [{"name": "Кофе Лаб", "posts": 6}]
    assert sum(w["competitor"] + w["market"] for w in d["weekly"]) == 10
    assert len(d["top_posts"]) == 6 and all(p["source"]["role"] != "own" for p in d["top_posts"])
    ops = [p["overperformance"] for p in d["top_posts"]]
    assert ops == sorted(ops, reverse=True) and d["own_posts"] == []
    assert d["formats_market"][0]["value"] in ("photo", "text") and d["related"][0]["topic"] == "продукт и услуги"
    assert d["subtopics"] == [] and d["insight"] is None
    assert (await acc.get("/api/v1/topics/detail", params={"topic": "нет такой"})).status_code == 404


class FakeGap:
    name = "fake"

    def __init__(self):
        self.calls, self.user = 0, None

    async def chat_json(self, model, system, user, *, temperature, max_tokens):
        self.calls += 1
        self.user = user
        return AIResult(data={"why": f"объяснение {self.calls}", "evidence": ["83% рынка против 0% у вас"],
                              "how_to_cover": [{"angle": "свои кейсы", "format": "пост", "headline": "Как мы…"}]},
                        model=model, provider="fake", usage={"cost": 0.004})


@pytest.fixture
def gap_ai(monkeypatch):
    from app.topics import service
    fake = FakeGap()
    monkeypatch.setattr(service, "AIRouter", lambda session: AIRouter(session, fake, backoff_sec=0))
    monkeypatch.setattr(settings, "openrouter_api_key", "key")
    return fake


async def test_explain_gap_cached_and_forced(client, session, gap_ai):
    acc, _ = await market_world(client, session)
    body = {"topic": "кейсы и результаты", "days": 30}
    r = await acc.post("/api/v1/topics/gaps/explain", json=body)
    assert r.status_code == 200 and r.json()["data"]["why"] == "объяснение 1" and r.json()["days"] == 30
    assert "Кофе Лаб" in gap_ai.user and "83.3" in gap_ai.user  # модели передаются цифры кода
    assert (await acc.post("/api/v1/topics/gaps/explain", json=body)).json()["data"]["why"] == "объяснение 1"
    assert gap_ai.calls == 1
    r = await acc.post("/api/v1/topics/gaps/explain", json={**body, "force": True})
    assert r.json()["data"]["why"] == "объяснение 2"
    gaps = (await acc.get("/api/v1/topics/gaps?days=30")).json()["gaps"]
    assert gaps[0]["insight"]["data"]["why"] == "объяснение 2"
    assert (await acc.post("/api/v1/topics/gaps/explain", json={"topic": "нет такой"})).status_code == 404


async def test_explain_roles_and_key(client, session, monkeypatch):
    acc, _ = await market_world(client, session)
    viewer = await _join(client, acc, "viewer")
    body = {"topic": "кейсы и результаты", "days": 30}
    assert (await viewer.post("/api/v1/topics/gaps/explain", json=body)).status_code == 403
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    assert (await acc.post("/api/v1/topics/gaps/explain", json=body)).status_code == 503


# --- под-темы ---

class FakeNamer:
    name = "fake"

    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    async def chat_json(self, model, system, user, *, temperature, max_tokens):
        import json
        self.calls += 1
        if self.fail:
            raise AIError("HTTP 500", retryable=False)
        groups = json.loads(user)
        return AIResult(data={"clusters": [{"id": g["id"], "label": f"Под-тема {g['id']}", "description": "о группе",
                                            "keywords": ["a", "b"]} for g in groups]},
                        model=model, provider="fake", usage={"cost": 0.002})


async def run_cluster(acc, session, namer=None) -> dict:
    job = Job(organization_id=acc.org_id, kind="cluster_topics", params={})
    session.add(job)
    await session.commit()

    async def handler(s, j):
        return await cluster.handle_cluster_topics(
            s, j, router=AIRouter(s, namer, backoff_sec=0) if namer else None)
    await run_job(job.id, handler)
    return (await acc.get(f"/api/v1/jobs/{job.id}")).json()


async def test_clustering_finds_subtopics_and_names_them(client, session):
    acc = await register(client)
    await add_posts(session, acc.org_id, role=SourceRole.competitor, topic="кейсы и результаты", n=30, groups=3)
    await add_posts(session, acc.org_id, role=SourceRole.own, topic="кейсы и результаты", n=6, groups=1)
    await add_posts(session, acc.org_id, role=SourceRole.market, topic="мало постов", n=5, groups=3)
    namer = FakeNamer()
    job = await run_cluster(acc, session, namer)
    assert job["status"] == "completed", job
    assert job["result"]["subtopics"] == 3 and job["result"]["topics_checked"] == 1 and namer.calls == 1

    topics = {t["topic"]: t for t in (await acc.get("/api/v1/topics?days=30")).json()["topics"]}
    subs = topics["кейсы и результаты"]["subtopics"]
    assert len(subs) == 3 and sum(s["size"] for s in subs) >= 30 and subs[0]["label"].startswith("Под-тема")
    detail = (await acc.get("/api/v1/topics/detail", params={"topic": "кейсы и результаты"})).json()
    assert sum(s["own"] for s in detail["subtopics"]) == 6  # свои посты попали в группу своего центра
    assert topics["мало постов"]["subtopics"] == []

    again = await run_cluster(acc, session, FakeNamer())  # пересчёт заменяет, а не добавляет
    assert again["status"] == "completed"
    count = (await session.execute(select(func.count()).select_from(TopicCluster).where(
        TopicCluster.organization_id == acc.org_id))).scalar_one()
    assert count == 3


async def test_clustering_without_model_uses_keywords(client, session):
    acc = await register(client)
    texts = ["обжарка зерна эфиопия светлая", "обжарка зерна кения средняя", "обжарка зерна бразилия тёмная"]
    await add_posts(session, acc.org_id, role=SourceRole.market, topic="зерно", n=30, groups=3, texts=texts)
    job = await run_cluster(acc, session, FakeNamer(fail=True))
    assert job["status"] == "completed" and "частым словам" in job["result"]["message"]
    labels = [c.label for c in (await session.execute(select(TopicCluster).where(
        TopicCluster.organization_id == acc.org_id))).scalars()]
    # в названии — отличительные слова группы, а не общие для всех («обжарка», «зерна»)
    for country in ("эфиопия", "кения", "бразилия"):
        assert sum(country in lbl for lbl in labels) == 1
    assert not any("обжарка" in lbl for lbl in labels)


async def test_clustering_needs_data_and_endpoint(client, session):
    acc = await register(client)
    job = await run_cluster(acc, session)
    assert job["status"] == "failed" and job["error"].startswith("Недостаточно данных")

    r = await acc.post("/api/v1/topics/cluster")
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert (await acc.post("/api/v1/topics/cluster")).status_code == 409
    assert (await acc.get("/api/v1/topics")).json()["cluster_job"]["status"] == "queued"


async def test_schedule_due_once_per_week(client, session):
    acc = await register(client)
    await add_posts(session, acc.org_id, role=SourceRole.market, topic="кейсы и результаты", n=15)

    def jobs():
        return select(func.count()).select_from(Job).where(Job.organization_id == acc.org_id,
                                                           Job.kind == "cluster_topics")
    await cluster.schedule_due(session, None)
    assert (await session.execute(jobs())).scalar_one() == 1
    await cluster.schedule_due(session, None)
    assert (await session.execute(jobs())).scalar_one() == 1


async def test_topics_isolated(client, session):
    a, _ = await market_world(client, session)
    b = await register(client)
    assert (await b.get("/api/v1/topics?days=30")).json()["topics"] == []
    assert (await b.get("/api/v1/topics/gaps?days=30")).json()["gaps"] == []
    assert (await b.get("/api/v1/topics/detail", params={"topic": "кейсы и результаты"})).status_code == 404
    viewer = await _join(client, a, "viewer")
    assert (await viewer.post("/api/v1/topics/cluster")).status_code == 403
