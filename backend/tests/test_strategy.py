import json
import uuid

from sqlalchemy import select

from app.ai.openrouter import AIError, AIResult
from app.ai.router import AIRouter
from app.analysis import taxonomy
from app.models import AuditItem, Competitor, ContentAudit, Job, JobStatus, SourceRole
from app.strategy import candidates, opportunities
from app.workers.tasks import run_job
from tests.conftest import register, set_plan
from tests.test_roles import _join
from tests.test_topics import add_posts


class FakeStrategist:
    name = "fake"

    def __init__(self, fail: Exception | None = None):
        self.fail, self.inputs = fail, []

    async def chat_json(self, model, system, user, *, temperature, max_tokens):
        data = json.loads(user)
        self.inputs.append(data)
        if self.fail:
            raise self.fail
        c1, c2 = data["кандидаты"][0]["id"], data["кандидаты"][1]["id"]
        opps = [
            {"candidate": c1, "title": "Как клиент сэкономил 30% на закупках", "why": "Пробел и рост",
             "angle": "Свой разбор", "formats": ["Telegram", "email", "tiktok"], "funnel_stage": "доказательство",
             "target_role": "Специалист", "fixes": ["value", "unknown"]},
            {"candidate": c1, "title": "Разбор ошибки внедрения", "formats": ["article"]},
            {"candidate": c1, "title": "Третья тема из того же кандидата"},  # лимит 2 на кандидата
            {"candidate": c1, "title": "как клиент сэкономил 30% на закупках"},  # дубль
            {"candidate": "c99", "title": "Тема без данных рынка"},
            {"candidate": c2, "title": "Чек-лист выбора подрядчика", "funnel_stage": "непонятно"},
        ]
        return AIResult(data={"opportunities": opps}, model=model, provider=self.name,
                        usage={"prompt_tokens": 3000, "completion_tokens": 800, "cost": 0.01})


def handler(provider=None):
    async def run(session, job):
        router = AIRouter(session, provider, backoff_sec=0) if provider else None
        return await opportunities.handle_build_opportunities(session, job, router=router)
    return run


async def world(client, session):
    acc = await register(client, f"Кофейня-{uuid.uuid4().hex[:4]}")
    await set_plan(acc.org_id, "starter")
    comp = Competitor(organization_id=acc.org_id, name="Кофе Лаб")
    session.add(comp)
    await session.commit()
    await add_posts(session, acc.org_id, role=SourceRole.own, topic="продукт и услуги", n=6)
    await add_posts(session, acc.org_id, role=SourceRole.competitor, topic="кейсы и результаты", n=12,
                    competitor_id=comp.id)
    await add_posts(session, acc.org_id, role=SourceRole.market, topic="мероприятия", n=8)
    await add_posts(session, acc.org_id, role=SourceRole.market, topic="продукт и услуги", n=4)
    return acc


async def generate(acc, provider=None, **body) -> dict:
    r = await acc.post("/api/v1/strategy/opportunities/generate", json=body)
    assert r.status_code == 202, r.text
    await run_job(r.json()["id"], handler(provider))
    return (await acc.get(f"/api/v1/jobs/{r.json()['id']}")).json()


# --- чистые функции ---

def test_score_rewards_gap_trend_engagement_and_penalizes_saturation():
    base = {"gap": 20.0, "trend_pp": 4.0, "median_er": 2.0, "saturation_per_week": 1.0}
    assert candidates.score(base, 1.0) == 20 + 2 + 10
    assert candidates.score({**base, "gap": -30.0, "trend_pp": -5.0}, 1.0) == 10
    assert candidates.score({**base, "median_er": None}, 1.0) == 22
    assert candidates.score({**base, "saturation_per_week": 12.0}, None) == 19


def test_normalize_filters_and_limits():
    tax = taxonomy.OrgTaxonomy(topics=["a", "другое"], roles=["специалист", "широкая аудитория"], version=1, niche="x")
    cands = {"c1": {"id": "c1", "topic": "a", "market": {}, "examples": [], "score": 5}}
    out = opportunities.normalize([
        {"candidate": "c1", "title": "  Тема   один ", "formats": ["VK", "vk", "tiktok"], "funnel_stage": "Захват лида",
         "target_role": "Специалист", "fixes": ["cta", "cta", "x"]},
        {"candidate": "c2", "title": "нет такого кандидата"},
        {"candidate": "c1", "title": ""},
    ], cands, tax, ai=True)
    assert len(out) == 1
    o = out[0]
    assert o["title"] == "Тема один" and o["formats"] == ["vk"] and o["funnel_stage"] == "захват_лида"
    assert o["target_role"] == "специалист" and o["fixes"] == ["cta"] and o["topic"] == "a"


# --- генерация ---

async def test_generate_with_ai_attaches_market_evidence(client, session):
    acc = await world(client, session)
    ai = FakeStrategist()
    job = await generate(acc, ai)
    assert job["status"] == "completed", job
    assert "message" not in job["result"]

    items = (await acc.get("/api/v1/strategy/opportunities")).json()["items"]
    titles = [i["title"] for i in items]
    assert titles[:3] == ["Как клиент сэкономил 30% на закупках", "Разбор ошибки внедрения",
                          "Чек-лист выбора подрядчика"]
    assert "Тема без данных рынка" not in titles and "Третья тема из того же кандидата" not in titles
    assert [i["rank"] for i in items] == list(range(1, len(items) + 1))
    first = items[0]
    assert first["ai"] and first["formats"] == ["telegram", "email"] and first["fixes"] == ["value"]
    assert first["funnel_stage"] == "доказательство" and first["target_role"] == "специалист"
    assert first["market"]["share_market"] > first["market"]["share_own"]
    assert first["examples"] and first["examples"][0]["post_id"] and first["examples"][0]["source"]
    assert items[2]["funnel_stage"] is None
    # третий кандидат («продукт и услуги») без пробела и роста — добирать им до 10 нечего
    assert len(items) == 3 and all(i["ai"] for i in items)

    cands = ai.inputs[0]["кандидаты"]
    assert cands[0]["тема"] == "кейсы и результаты"  # самый большой пробел
    assert all(c["тема"] != "другое" for c in cands) and ai.inputs[0]["аудит"] is None


async def test_audit_weak_spots_go_to_the_model(client, session):
    acc = await world(client, session)
    audit = ContentAudit(organization_id=acc.org_id, company="Кофейня", stage="done", score=45,
                         result={"problems": [{"title": "Нет кейсов"}]})
    session.add(audit)
    await session.flush()
    session.add_all([AuditItem(audit_id=audit.id, criterion="value", score=3, weight=0.2, explanation="мало кейсов"),
                     AuditItem(audit_id=audit.id, criterion="strategy", score=8, weight=0.2, explanation="ок")])
    await session.commit()
    ai = FakeStrategist()
    await generate(acc, ai, audit_id=audit.id)
    ctx = ai.inputs[0]["аудит"]
    assert [c["ключ"] for c in ctx["слабые_критерии"]] == ["value"] and ctx["проблемы"] == ["Нет кейсов"]
    items = (await acc.get("/api/v1/strategy/opportunities")).json()["items"]
    assert {i["audit_id"] for i in items} == {audit.id}


async def test_without_key_and_on_model_failure_uses_numbers(client, session):
    acc = await world(client, session)
    job = await generate(acc)
    assert "OPENROUTER_API_KEY" in job["result"]["message"]
    items = (await acc.get("/api/v1/strategy/opportunities")).json()["items"]
    # «продукт и услуги» у клиента и так больше, чем у рынка (score 0) — не предлагается
    assert [i["title"] for i in items] == ["Кейсы и результаты", "Мероприятия"]
    assert not any(i["ai"] for i in items)

    job = await generate(acc, FakeStrategist(fail=AIError("HTTP 500", retryable=False)))
    assert job["status"] == "completed" and "Модель недоступна" in job["result"]["message"]


async def test_not_enough_market_data(client, session):
    acc = await register(client)
    await add_posts(session, acc.org_id, role=SourceRole.market, topic="мероприятия", n=5)
    job = await generate(acc)
    assert job["status"] == "failed" and "Недостаточно данных" in job["error"]


async def test_regenerate_archives_new_keeps_taken(client, session):
    acc = await world(client, session)
    await generate(acc, FakeStrategist())
    first = (await acc.get("/api/v1/strategy/opportunities")).json()["items"]
    assert len(first) == 3
    r = await acc.patch(f"/api/v1/strategy/opportunities/{first[0]['id']}", json={"status": "in_factory"})
    assert r.status_code == 200 and r.json()["status"] == "in_factory"
    assert (await acc.patch(f"/api/v1/strategy/opportunities/{first[1]['id']}", json={"status": "archived"})
            ).status_code == 422
    await acc.patch(f"/api/v1/strategy/opportunities/{first[2]['id']}", json={"status": "dismissed"})

    await generate(acc)
    active = (await acc.get("/api/v1/strategy/opportunities")).json()["items"]
    assert first[0]["id"] in [i["id"] for i in active] and len(active) == 3  # 1 в работе + 2 новые по цифрам
    assert [i["id"] for i in (await acc.get("/api/v1/strategy/opportunities?view=archived")).json()["items"]] == [
        first[1]["id"]]
    assert [i["id"] for i in (await acc.get("/api/v1/strategy/opportunities?view=in_factory")).json()["items"]] == [
        first[0]["id"]]
    assert len((await acc.get("/api/v1/strategy/opportunities?view=dismissed")).json()["items"]) == 1


async def test_conflict_roles_and_isolation(client, session):
    acc = await world(client, session)
    other = await register(client)
    assert (await acc.post("/api/v1/strategy/opportunities/generate", json={})).status_code == 202
    r = await acc.post("/api/v1/strategy/opportunities/generate", json={})
    assert r.status_code == 409  # первая задача ещё в очереди
    job_id = (await acc.get("/api/v1/strategy/opportunities")).json()["job"]["id"]
    await run_job(job_id, handler())

    audit = ContentAudit(organization_id=other.org_id, company="Чужая", stage="done")
    session.add(audit)
    await session.commit()
    r = await acc.post("/api/v1/strategy/opportunities/generate", json={"audit_id": audit.id})
    assert r.status_code == 404

    item = (await acc.get("/api/v1/strategy/opportunities")).json()["items"][0]
    assert (await other.get("/api/v1/strategy/opportunities")).json() == {"items": [], "job": None}
    assert (await other.patch(f"/api/v1/strategy/opportunities/{item['id']}", json={"status": "done"})
            ).status_code == 404

    viewer = await _join(client, acc, "viewer")
    assert (await viewer.post("/api/v1/strategy/opportunities/generate", json={})).status_code == 403
    assert (await viewer.patch(f"/api/v1/strategy/opportunities/{item['id']}", json={"status": "done"})
            ).status_code == 403
    assert len((await viewer.get("/api/v1/strategy/opportunities")).json()["items"]) == 2


async def test_after_audit_starts_generation_only_for_org_audits_with_data(client, session):
    acc = await world(client, session)
    poor = await register(client)

    async def audit_job(org_id: int, public: bool = False) -> Job:
        audit = ContentAudit(organization_id=org_id, company="X", stage="done", is_public=public)
        session.add(audit)
        await session.flush()
        job = Job(organization_id=org_id, kind="run_audit", params={"audit_id": audit.id},
                  status=JobStatus.completed)
        session.add(job)
        await session.commit()
        return job

    async def started(org_id: int) -> int:
        rows = await session.execute(select(Job).where(Job.organization_id == org_id,
                                                       Job.kind == "build_opportunities"))
        return len(rows.scalars().all())

    await opportunities.after_audit((await audit_job(acc.org_id, public=True)).id, None)
    assert await started(acc.org_id) == 0
    await opportunities.after_audit((await audit_job(poor.org_id)).id, None)
    assert await started(poor.org_id) == 0
    job = await audit_job(acc.org_id)
    await opportunities.after_audit(job.id, None)
    new = (await session.execute(select(Job).where(Job.organization_id == acc.org_id,
                                                   Job.kind == "build_opportunities"))).scalar_one()
    assert new.params["audit_id"] == job.params["audit_id"]
