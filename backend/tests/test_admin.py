from datetime import timedelta

from sqlalchemy import select, update

from app.admin import cli
from app.core.config import settings
from app.core.db import SessionLocal, utcnow
from app.models import Digest, Job, JobStatus, LLMRequest, User
from tests.conftest import Account, register, set_plan

ADMIN = "/api/v1/admin"


async def superadmin(client) -> Account:
    acc = await register(client, "Сервис")
    assert await cli.set_superadmin(acc.email, True)
    return acc


async def add_llm(org_id: int | None, cost: float, *, ok: bool = True, model: str = "qwen/test",
                  operation: str = "classify", at=None) -> None:
    async with SessionLocal() as s:
        s.add(LLMRequest(organization_id=org_id, task="classify", operation=operation, provider="openrouter",
                         model=model, input_tokens=100, output_tokens=20, cost_usd=cost, latency_ms=500, ok=ok,
                         error=None if ok else "HTTP 500", at=at or utcnow()))
        await s.commit()


async def add_job(org_id: int, kind: str = "sync_source", status: JobStatus = JobStatus.failed, **kw) -> int:
    async with SessionLocal() as s:
        job = Job(organization_id=org_id, kind=kind, status=status, params={"source_id": 1},
                  error="boom" if status == JobStatus.failed else None,
                  finished_at=utcnow() if status == JobStatus.failed else None, **kw)
        s.add(job)
        await s.commit()
        return job.id


async def test_admin_hidden_from_regular_users(client):
    user = await register(client)
    for url in ("/overview", "/organizations", "/users", "/costs", "/jobs", "/errors", "/providers", "/actions"):
        assert (await user.get(ADMIN + url)).status_code == 404, url
    assert (await client.get(ADMIN + "/overview")).status_code == 401
    me = (await user.get("/api/v1/auth/me")).json()
    assert me["is_superadmin"] is False


async def test_superadmin_from_env_on_login(client, monkeypatch):
    email = "boss-env@example.com"
    monkeypatch.setattr(settings, "superadmin_emails", f"other@example.com, {email.upper()}")
    acc = await register(client, email=email)
    me = (await acc.get("/api/v1/auth/me")).json()
    assert me["is_superadmin"] is True
    assert (await acc.get(ADMIN + "/overview")).status_code == 200
    async with SessionLocal() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one()
        assert user.last_seen_at is not None


async def test_cli_grant_and_revoke(client):
    acc = await register(client)
    assert await cli.set_superadmin(acc.email, True)
    assert acc.email in await cli.superadmins()
    assert await cli.set_superadmin(acc.email, False)
    assert not await cli.set_superadmin("nobody@example.com", True)
    assert cli.main(["bogus"]) == 2


async def test_overview_and_org_list(client):
    admin = await superadmin(client)
    org = await register(client, "Рога и копыта")
    await set_plan(org.org_id, "professional")
    await add_llm(org.org_id, 0.25)
    await add_llm(org.org_id, 0.05, ok=False)
    await add_job(org.org_id, status=JobStatus.queued, created_at=utcnow() - timedelta(hours=1))

    ov = (await admin.get(ADMIN + "/overview")).json()
    assert ov["organizations"]["by_plan"]["professional"] >= 1
    assert ov["organizations"]["paying"] >= 1
    assert ov["ai"]["cost_month"] >= 0.3
    assert ov["ai"]["errors_24h"] >= 1
    assert ov["jobs"]["stuck"] >= 1
    assert len(ov["ai"]["by_day"]) == 30
    assert any(t["id"] == org.org_id for t in ov["top_orgs"])

    r = (await admin.get(ADMIN + "/organizations", params={"q": "копыта"})).json()
    assert r["total"] == 1
    item = r["items"][0]
    assert item["plan"] == "professional" and item["owner_email"] == org.email
    assert item["ai_cost_month"] == 0.3 and item["members"] == 1
    # поиск по email участника
    r = (await admin.get(ADMIN + "/organizations", params={"q": org.email})).json()
    assert [i["id"] for i in r["items"]] == [org.org_id]
    r = (await admin.get(ADMIN + "/organizations", params={"sort": "cost", "per_page": 1})).json()
    assert len(r["items"]) == 1 and r["total"] >= 2


async def test_org_detail(client):
    admin = await superadmin(client)
    org = await register(client, "Детали")
    await add_llm(org.org_id, 0.1, model="qwen/max", operation="audit")
    await add_llm(org.org_id, 0.0, ok=False, operation="audit")
    await add_job(org.org_id)
    d = (await admin.get(ADMIN + f"/organizations/{org.org_id}")).json()
    assert d["subscription"]["effective_plan"] == "starter"
    assert d["members"][0]["email"] == org.email and d["members"][0]["role"] == "owner"
    assert d["jobs"][0]["retryable"] is True
    assert d["ai_by_operation"][0]["key"] == "audit" and d["ai_by_operation"][0]["calls"] == 2
    assert d["llm_errors"][0]["error"] == "HTTP 500"
    assert len(d["ai_by_day"]) == 30
    assert (await admin.get(ADMIN + "/organizations/999999")).status_code == 404


async def test_manual_plan_change_applies_to_quotas(client):
    admin = await superadmin(client)
    org = await register(client, "Клиент")
    await set_plan(org.org_id, "free")  # Free: 1 участник — приглашение упирается в лимит
    r = await org.post("/api/v1/organizations/current/invitations", json={"email": "a@example.com"})
    assert r.status_code == 402

    until = (utcnow() + timedelta(days=30)).isoformat()
    r = await admin.put(ADMIN + f"/organizations/{org.org_id}/subscription",
                        json={"plan_code": "starter", "current_period_end": until, "note": "счёт №12"})
    assert r.status_code == 200, r.text
    assert r.json()["effective_plan"] == "starter" and r.json()["limits"]["members"] == 2
    assert (await org.post("/api/v1/organizations/current/invitations",
                           json={"email": "a@example.com"})).status_code == 201
    usage = (await org.get("/api/v1/billing/usage")).json()
    assert usage["plan"] == "starter" and usage["plan_until"]

    # индивидуальный лимит поверх тарифа
    r = await admin.put(ADMIN + f"/organizations/{org.org_id}/subscription",
                        json={"plan_code": "starter", "limits_override": {"competitors": 0, "sources": None}})
    assert r.json()["limits"]["competitors"] == 0 and r.json()["limits"]["sources"] is None
    assert (await org.post("/api/v1/competitors", json={"name": "X"})).status_code == 402

    # срок истёк — организация снова на тарифе по умолчанию (Starter)
    past = (utcnow() - timedelta(days=1)).isoformat()
    r = await admin.put(ADMIN + f"/organizations/{org.org_id}/subscription",
                        json={"plan_code": "agency", "current_period_end": past})
    assert r.json()["effective_plan"] == "starter" and r.json()["active"] is False
    assert (await org.get("/api/v1/billing/usage")).json()["plan"] == "starter"

    bad = await admin.put(ADMIN + f"/organizations/{org.org_id}/subscription", json={"plan_code": "gold"})
    assert bad.status_code == 422
    bad = await admin.put(ADMIN + f"/organizations/{org.org_id}/subscription",
                          json={"plan_code": "starter", "limits_override": {"rockets": 1}})
    assert bad.status_code == 422

    log = (await admin.get(ADMIN + "/actions")).json()["items"]
    mine = [a for a in log if a["organization_id"] == org.org_id]
    assert len(mine) == 3 and mine[-1]["details"]["after"]["plan_code"] == "starter"
    assert mine[-1]["admin"] == admin.email


async def test_block_user_and_superadmin_rules(client):
    admin = await superadmin(client)
    user = await register(client, "Нарушитель")
    r = await admin.patch(ADMIN + f"/users/{(await user.get('/api/v1/auth/me')).json()['id']}",
                          json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert (await user.get("/api/v1/auth/me")).status_code == 401
    login = await client.post("/api/v1/auth/login", json={"email": user.email, "password": "password123"})
    assert login.status_code == 401

    blocked = (await admin.get(ADMIN + "/users", params={"flag": "blocked", "q": user.email})).json()
    assert blocked["total"] == 1 and blocked["items"][0]["organizations"][0]["role"] == "owner"

    my_id = (await admin.get("/api/v1/auth/me")).json()["id"]
    assert (await admin.patch(ADMIN + f"/users/{my_id}", json={"is_superadmin": False})).status_code == 409
    assert (await admin.patch(ADMIN + "/users/999999", json={"is_active": True})).status_code == 404


async def test_costs(client):
    admin = await superadmin(client)
    org = await register(client, "Расходы")
    await add_llm(org.org_id, 1.5, model="qwen/costly", operation="digest")
    await add_llm(None, 0.5, model="qwen/costly", operation="embed")
    await add_llm(org.org_id, 9.0, at=utcnow() - timedelta(days=60))  # вне периода
    c = (await admin.get(ADMIN + "/costs", params={"days": 7})).json()
    assert len(c["by_day"]) == 7
    model = next(m for m in c["by_model"] if m["key"] == "qwen/costly")
    assert model["cost_usd"] == 2.0 and model["calls"] == 2
    org_row = next(o for o in c["by_org"] if o["key"] == org.org_id)
    assert org_row["cost_usd"] == 1.5 and org_row["name"] == "Расходы"
    assert any(o["key"] is None and o["name"] == "без организации" for o in c["by_org"])
    assert c["totals"]["cost_usd"] >= 2.0


async def test_jobs_cancel_and_retry(client):
    admin = await superadmin(client)
    org = await register(client, "Задачи")
    failed = await add_job(org.org_id)
    running = await add_job(org.org_id, kind="cluster_topics", status=JobStatus.running)
    audit = await add_job(org.org_id, kind="run_audit")

    r = (await admin.get(ADMIN + "/jobs", params={"org_id": org.org_id})).json()
    assert r["total"] == 3 and r["items"][0]["organization"] == "Задачи"
    assert "sync_source" in r["kinds"]
    assert (await admin.get(ADMIN + "/jobs", params={"org_id": org.org_id, "status": "active"})).json()["total"] == 1

    r = await admin.post(ADMIN + f"/jobs/{failed}/retry")
    assert r.status_code == 202, r.text
    new = r.json()
    assert new["id"] != failed and new["status"] == "queued" and new["params"] == {"source_id": 1}
    assert new["queued"] is False  # в тестах нет Redis
    assert (await admin.post(ADMIN + f"/jobs/{audit}/retry")).status_code == 409
    assert (await admin.post(ADMIN + f"/jobs/{running}/retry")).status_code == 409

    r = await admin.post(ADMIN + f"/jobs/{running}/cancel")
    assert r.json()["status"] == "cancelled"
    assert (await admin.post(ADMIN + f"/jobs/{running}/cancel")).status_code == 409
    kinds = {a["action"] for a in (await admin.get(ADMIN + "/actions")).json()["items"]}
    assert {"job.retry", "job.cancel"} <= kinds


async def test_errors_feed(client):
    admin = await superadmin(client)
    org = await register(client, "Ошибки")
    await add_job(org.org_id)
    await add_llm(org.org_id, 0, ok=False)
    async with SessionLocal() as s:
        now = utcnow()
        s.add(Digest(organization_id=org.org_id, period_from=now - timedelta(days=7), period_to=now, days=7,
                     trigger="manual", stats={}, sections={}, ai=False, email_error="SMTP 535"))
        await s.commit()
    e = (await admin.get(ADMIN + "/errors")).json()
    types = {i["type"] for i in e["items"] if i["organization_id"] == org.org_id}
    assert {"job", "llm", "email"} <= types
    assert e["counts"]["job"] >= 1
    only = (await admin.get(ADMIN + "/errors", params={"kind": "email"})).json()
    assert {i["type"] for i in only["items"]} == {"email"}


async def test_providers(client):
    admin = await superadmin(client)
    await add_llm(None, 0.01, model="qwen/health")
    await add_llm(None, 0, ok=False, model="qwen/health")
    p = (await admin.get(ADMIN + "/providers")).json()
    assert p["llm"]["configured"] is False  # в тестах ключа нет
    assert p["llm"]["models"]["analyze"] == settings.llm_model_analyze
    h = next(m for m in p["llm"]["health_24h"] if m["model"] == "qwen/health")
    assert h["calls"] == 2 and h["error_rate"] == 0.5 and h["p50_latency_ms"] == 500
    kinds = {c["kind"]: c for c in p["connectors"]}
    assert kinds["telegram"]["configured"] is True
    assert kinds["instagram"]["key_setting"] == "APIFY_TOKEN"
    assert p["infra"]["queue"] is False
    assert "configured" in p["email"]


async def test_deactivated_superadmin_loses_access(client):
    admin = await superadmin(client)
    async with SessionLocal() as s:
        await s.execute(update(User).where(User.email == admin.email).values(is_superadmin=False))
        await s.commit()
    assert (await admin.get(ADMIN + "/overview")).status_code == 404
