import json
import uuid

import pytest
from sqlalchemy import func, select

from app.ai.openrouter import AIError, AIResult
from app.ai.router import AIRouter
from app.audits import pipeline as audit_pipeline
from app.audits import public
from app.audits.criteria import CRITERIA, heuristics, overall
from app.core.config import settings
from app.models import ContentAudit, GlobalPost, GlobalSource, PostAnalysis, SourceRole, UsageEvent
from app.workers.tasks import run_job
from tests.conftest import register, set_plan
from tests.test_analysis import FakeClassifier, make_source
from tests.test_sources import handle, web  # noqa: F401 — фикстура «интернета»

KEYS = [c.key for c in CRITERIA]


class FakeAuditAI(FakeClassifier):
    """Классификатор + аудитор: по системному промпту понимает, какой агент вызван."""

    def __init__(self, audit_fail: Exception | None = None):
        super().__init__()
        self.audit_fail = audit_fail
        self.audit_inputs: list[dict] = []

    async def chat_json(self, model, system, user, *, temperature, max_tokens):
        if "аудитор контент" not in system:
            return await super().chat_json(model, system, user, temperature=temperature, max_tokens=max_tokens)
        data = json.loads(user)
        self.audit_inputs.append(data)
        if self.audit_fail:
            raise self.audit_fail
        first = data["примеры_публикаций"][0]["id"]
        criteria = {k: {"score": s, "explanation": f"оценка {k}",
                        "evidence": [{"fact": "пост с цифрой", "post_id": first},
                                     {"fact": "выдуманный пост", "post_id": 999_999_999}, {"fact": ""}],
                        "recommendations": ["сделать А", "", "сделать Б", "сделать В", "лишнее"]}
                    for k, s in zip(KEYS, [7, 12, None, 4, 3.5, 5], strict=True)}
        return AIResult(data={"criteria": criteria, "summary": "Итог аудита", "strengths": ["Регулярность"],
                              "problems": [{"title": "Мало кейсов", "detail": "…", "criterion": "value"},
                                           {"title": "Нет лид-магнита", "criterion": "неизвестно"}]},
                        model=model, provider="fake", usage={"prompt_tokens": 5000, "completion_tokens": 900,
                                                             "cost": 0.01})


def handler(provider=None):
    async def run(session, job):
        router = AIRouter(session, provider, backoff_sec=0) if provider else None
        return await audit_pipeline.handle_run_audit(session, job, router=router)
    return run


@pytest.fixture
def long_period(monkeypatch):
    monkeypatch.setattr(settings, "audit_days", 36500)  # фикстуры t.me датированы 2026 годом


async def run_audit(acc, audit: dict, provider=None) -> dict:
    await run_job(audit["job_id"], handler(provider))
    return (await acc.get(f"/api/v1/audits/{audit['id']}")).json()


async def market_source(session, org_id: int, n: int, topic: str):
    src = await make_source(session, org_id, [{"views": 1000, "likes": 10 * (i % 5 + 1)} for i in range(n)])
    src.role = SourceRole.competitor
    posts = (await session.execute(select(GlobalPost).where(
        GlobalPost.global_source_id == src.global_source_id))).scalars().all()
    for p in posts:
        p.engagement, p.er = p.likes, 100 * p.likes / p.views
        session.add(PostAnalysis(organization_id=org_id, post_id=p.id, taxonomy_version=0, content_type="кейс",
                                 funnel_stage="продажа", hook_type="вопрос", cta_type="заявка", proof_type="нет",
                                 tone="экспертный", value_type="обучение", topic=topic, target_role="специалист",
                                 has_case=True, has_numbers=False, has_offer=False, has_lead_magnet=True))
    await session.commit()
    return src


# --- чистые функции ---

def test_overall_is_weighted_and_skips_missing():
    assert overall({k: 10 for k in KEYS}) == 100
    assert overall({"strategy": 5, "hook": None}) == 50
    assert overall({k: None for k in KEYS}) is None
    assert overall({"strategy": 10, "value": 0}) == 50  # веса равны (0.20 и 0.20)


def test_heuristics_without_posts_and_with_labels():
    empty = heuristics({"posts": 0}, None)
    assert all(v["score"] is None for v in empty.values())
    assert heuristics({"posts": 0}, {"forms": 2})["cta"]["score"] == 3

    own = {"posts": 40, "analyzed": 20, "posts_per_week": 5, "days_since_last_post": 1,
           "funnel": [{"value": "охват", "share": 50}, {"value": "продажа", "share": 30}],
           "hook_share": 70, "case_share": 60, "numbers_share": 60, "cta_share": 30, "lead_magnet_share": 10}
    h = heuristics(own, {"forms": 1})
    assert h["strategy"]["score"] == 8 and h["hook"]["score"] == 7 and h["value"]["score"] == 10
    assert h["cta"]["score"] == 10 and h["audience_fit"]["score"] is None


# --- аудит организации ---

async def test_audit_with_ai_scores_evidence_and_usage(client, session, web, long_period):  # noqa: F811
    acc = await register(client)
    tg = handle()
    r = await acc.post("/api/v1/audits", json={"company": "Нейроника", "sources": [f"https://t.me/{tg}"]})
    assert r.status_code == 201, r.text
    audit = r.json()
    assert audit["status"] == "queued" and audit["inputs"] == [f"https://t.me/{tg}"]

    ai = FakeAuditAI()
    done = await run_audit(acc, audit, ai)
    assert done["status"] == "completed" and done["stage"] == "done", done
    items = {i["criterion"]: i for i in done["items"]}
    assert list(items) == KEYS and all(i["ai"] for i in items.values())
    assert items["audience_fit"]["score"] == 10 and items["hook"]["score"] is None  # 12 → 10, null остаётся
    assert done["score"] == overall({k: i["score"] for k, i in items.items()}) == 58.5

    ev = items["strategy"]["evidence"]
    assert len(ev) == 2  # пустой факт отброшен
    assert ev[0]["post_id"] and ev[0]["url"].startswith("https://t.me/")
    assert "post_id" not in ev[1]  # выдуманный id не превращается в ссылку
    assert items["strategy"]["recommendations"] == ["сделать А", "сделать Б"]

    res = done["result"]
    assert res["ai"] and res["summary"] == "Итог аудита"
    assert res["problems"][1]["criterion"] is None
    assert res["own"]["posts"] > 0 and res["own"]["analyzed"] > 0
    assert res["channels"][0]["kind"] == "telegram" and res["channels"][0]["origin"] == "input"
    bm = res["benchmark"]
    assert bm["enough"] is False and "Недостаточно данных" in bm["message"] and bm["rows"] == []
    assert res["gaps"] == []
    # модель не получила медиан рынка, которых нет
    assert isinstance(ai.audit_inputs[0]["сравнение_с_рынком"], str)

    audits = (await session.execute(select(func.count()).select_from(UsageEvent).where(
        UsageEvent.organization_id == acc.org_id, UsageEvent.metric == "audits"))).scalar_one()
    assert audits == 1
    assert [a["id"] for a in (await acc.get("/api/v1/audits")).json()] == [audit["id"]]


async def test_benchmark_and_gaps_with_enough_market_data(client, session, web, long_period):  # noqa: F811
    acc = await register(client)
    await set_plan(acc.org_id, "starter")
    await market_source(session, acc.org_id, 35, "мероприятия")
    audit = (await acc.post("/api/v1/audits", json={"sources": [f"@{handle()}"]})).json()
    done = await run_audit(acc, audit, FakeAuditAI())

    bm = done["result"]["benchmark"]
    assert bm["enough"] is True and bm["posts"] == 35 and bm["sources"] == 1
    rows = {r["key"]: r for r in bm["rows"]}
    assert rows["median_er"]["market"] == 3.0 and rows["median_er"]["top"] >= 4
    assert rows["lead_magnet_share"]["market"] == 100 and rows["cta_share"]["top"] == 100
    gaps = done["result"]["gaps"]
    assert gaps[0]["topic"] == "мероприятия" and gaps[0]["share_own"] == 0 and gaps[0]["gap"] == 100


async def test_audit_of_competitor_excludes_it_from_market(client, session, web, long_period):  # noqa: F811
    acc = await register(client)
    src = await market_source(session, acc.org_id, 35, "мероприятия")
    gs_url = f"https://t.me/{(await session.get(GlobalSource, src.global_source_id)).key}"
    audit = (await acc.post("/api/v1/audits", json={"sources": [gs_url], "use_own_sources": False})).json()
    done = await run_audit(acc, audit)
    assert done["result"]["benchmark"]["posts"] == 0


async def test_without_ai_key_scores_by_metrics(client, web, long_period):  # noqa: F811
    acc = await register(client)
    audit = (await acc.post("/api/v1/audits", json={"sources": [f"@{handle()}"]})).json()
    done = await run_audit(acc, audit)  # ключа нет — router не создаётся
    assert done["status"] == "completed"
    items = {i["criterion"]: i for i in done["items"]}
    assert not any(i["ai"] for i in items.values())
    assert items["strategy"]["score"] is not None
    assert items["audience_fit"]["score"] is None and items["differentiation"]["score"] is None
    assert "OPENROUTER_API_KEY" in done["result"]["notes"][0]
    assert done["result"]["own"]["analyzed"] == 0


async def test_model_failure_falls_back_to_metrics(client, web, long_period):  # noqa: F811
    acc = await register(client)
    audit = (await acc.post("/api/v1/audits", json={"sources": [f"@{handle()}"]})).json()
    done = await run_audit(acc, audit, FakeAuditAI(audit_fail=AIError("HTTP 500", retryable=False)))
    assert done["status"] == "completed" and done["result"]["ai"] is False
    assert done["result"]["own"]["analyzed"] > 0  # разметка прошла, упал только аудитор
    assert any("Модель недоступна" in n for n in done["result"]["notes"])
    assert done["result"]["problems"]  # проблемы — по слабым критериям из цифр
    assert done["score"] is not None


async def test_nothing_collected_fails_with_reason(client, web, long_period):  # noqa: F811
    acc = await register(client)
    web.down = True
    audit = (await acc.post("/api/v1/audits", json={"sources": [f"@{handle()}"]})).json()
    done = await run_audit(acc, audit)
    assert done["status"] == "failed" and "Не удалось собрать данные" in done["error"]


async def test_website_only_discovers_social_channels(client, web, long_period):  # noqa: F811
    acc = await register(client)
    audit = (await acc.post("/api/v1/audits", json={"website": "example.com"})).json()
    assert audit["website"] == "https://example.com/"
    done = await run_audit(acc, audit, FakeAuditAI())
    res = done["result"]
    assert res["site"]["title"] == "Компания" and res["site"]["pages"]
    kinds = {(c["kind"], c["origin"]) for c in res["channels"]}
    assert ("website", "input") in kinds and ("telegram", "site") in kinds
    assert not any(c["kind"] == "instagram" for c in res["channels"])  # без APIFY_TOKEN не подхватываем


async def test_validation_own_sources_and_quota(client, session):
    acc = await register(client)
    r = await acc.post("/api/v1/audits", json={})
    assert r.status_code == 422 and "Укажите сайт" in r.json()["detail"]
    assert (await acc.post("/api/v1/audits", json={"sources": ["@x"]})).status_code == 422  # слишком короткий хэндл

    src = await make_source(session, acc.org_id, [{}])
    src.role = SourceRole.own
    await session.commit()
    r = await acc.post("/api/v1/audits", json={})
    assert r.status_code == 201, r.text
    assert r.json()["use_own_sources"] is True and r.json()["company"] == "Org"


async def test_quota_and_viewer(client, session):
    from tests.test_roles import _join

    acc = await register(client)
    await set_plan(acc.org_id, "starter")
    viewer = await _join(client, acc, "viewer")
    await set_plan(acc.org_id, "free")  # 1 аудит в месяц
    assert (await acc.post("/api/v1/audits", json={"sources": [f"@{handle()}"]})).status_code == 201
    r = await acc.post("/api/v1/audits", json={"sources": [f"@{handle()}"]})
    assert r.status_code == 402 and r.json()["detail"]["metric"] == "audits_month"

    assert (await viewer.post("/api/v1/audits", json={"sources": [f"@{handle()}"]})).status_code == 403
    assert len((await viewer.get("/api/v1/audits")).json()) == 1


async def test_audits_isolated(client, web):  # noqa: F811
    a = await register(client)
    b = await register(client)
    audit = (await a.post("/api/v1/audits", json={"sources": [f"@{handle()}"]})).json()
    assert (await b.get(f"/api/v1/audits/{audit['id']}")).status_code == 404
    assert (await b.delete(f"/api/v1/audits/{audit['id']}")).status_code == 404
    assert (await b.get("/api/v1/audits")).json() == []
    assert (await a.delete(f"/api/v1/audits/{audit['id']}")).status_code == 204


# --- публичный аудит ---

@pytest.fixture
def ip(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    return {"X-Forwarded-For": f"10.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"}


async def test_public_audit_redacted_limited_and_claimed(client, session, web, long_period, ip):  # noqa: F811
    r = await client.post("/api/v1/public/audits", headers=ip,
                          json={"company": "Нейроника", "sources": [f"@{handle()}"], "email": "Lead@Example.com"})
    assert r.status_code == 201, r.text
    token, job_id = r.json()["token"], r.json()["job_id"]
    assert r.json()["locked"] is True

    again = await client.post("/api/v1/public/audits", headers=ip, json={"company": "X", "sources": [f"@{handle()}"]})
    assert again.status_code == 429
    other_ip = {"X-Forwarded-For": "192.0.2.77"}
    same_email = await client.post("/api/v1/public/audits", headers=other_ip,
                                   json={"company": "X", "sources": [f"@{handle()}"], "email": "lead@example.com"})
    assert same_email.status_code == 429

    await run_job(job_id, handler(FakeAuditAI()))
    shown = (await client.get(f"/api/v1/public/audits/{token}")).json()
    assert shown["status"] == "completed" and shown["score"] is not None
    opened = [i for i in shown["items"] if not i["locked"]]
    assert len(opened) == public.OPEN_CRITERIA and all(i["explanation"] for i in opened)
    assert all(i["score"] is not None or i["locked"] for i in shown["items"])
    locked = [i for i in shown["items"] if i["locked"]]
    assert all(not i["explanation"] and not i["evidence"] for i in locked)
    res = shown["result"]
    assert res["gaps"] == [] and res["top_posts"] == [] and "rows" not in res["benchmark"]

    audit = (await session.execute(select(ContentAudit).where(ContentAudit.public_token == token))).scalar_one()
    assert audit.email == "lead@example.com" and audit.ip_hash and "10." not in audit.ip_hash

    acc = await register(client)
    claimed = await acc.post("/api/v1/audits/claim", json={"token": token})
    assert claimed.status_code == 200, claimed.text
    full = claimed.json()
    assert not full["locked"] and all(i["explanation"] for i in full["items"])
    assert [a["id"] for a in (await acc.get("/api/v1/audits")).json()] == [full["id"]]
    assert (await client.get(f"/api/v1/public/audits/{token}")).status_code == 404
    assert (await acc.post("/api/v1/audits/claim", json={"token": token})).status_code == 404


async def test_public_validation_honeypot_and_captcha(client, monkeypatch, ip):
    assert (await client.post("/api/v1/public/audits", headers=ip, json={"company": "X"})).status_code == 422
    r = await client.post("/api/v1/public/audits", headers=ip,
                          json={"company": "X", "sources": [f"@{handle()}"], "hp": "bot"})
    assert r.status_code == 400
    monkeypatch.setattr(settings, "turnstile_secret", "secret")
    r = await client.post("/api/v1/public/audits", headers=ip, json={"company": "X", "sources": [f"@{handle()}"]})
    assert r.status_code == 400 and "робот" in r.json()["detail"]


async def test_public_daily_cap(client, monkeypatch, ip):
    monkeypatch.setattr(settings, "public_audits_per_day", 0)
    r = await client.post("/api/v1/public/audits", headers=ip, json={"company": "X", "sources": [f"@{handle()}"]})
    assert r.status_code == 429 and "на сегодня" in r.json()["detail"]
