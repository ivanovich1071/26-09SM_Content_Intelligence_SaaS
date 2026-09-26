import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.ai.openrouter import AIError, AIResult
from app.ai.router import AIRouter
from app.core.config import settings
from app.digests import generator, mailer, render
from app.models import (
    Competitor,
    DigestSchedule,
    GlobalPost,
    Job,
    JobStatus,
    SourceRole,
    Website,
    WebsiteChange,
    WebsitePage,
)
from app.workers.tasks import run_job
from tests.conftest import register, set_plan
from tests.test_roles import _join
from tests.test_topics import add_posts


class FakeDigestAI:
    name = "fake"

    def __init__(self, fail: Exception | None = None):
        self.fail, self.inputs = fail, []

    async def chat_json(self, model, system, user, *, temperature, max_tokens):
        self.inputs.append((system, json.loads(user)))
        if self.fail:
            raise self.fail
        return AIResult(data={"headline": "Конкуренты ушли в кейсы", "summary": "Итог периода", "market": "Рынок вырос",
                              "topics": "", "competitors": "Кофе Лаб активнее всех", "top_posts": "…", "unusual": "…",
                              "own": "…", "recommendations": [{"title": "Сделать кейс", "why": "пробел"}, {"x": 1}],
                              "ideas": [{"title": "Кейс клиента", "why": "растёт", "format": "tiktok"}]},
                        model=model, provider=self.name, usage={"prompt_tokens": 3000, "completion_tokens": 900,
                                                                "cost": 0.01})


def handler(ai=None):
    async def run(session, job):
        router = AIRouter(session, ai, backoff_sec=0) if ai else None
        return await generator.handle_generate_digest(session, job, router=router)
    return run


async def world(client, session):
    acc = await register(client)
    await set_plan(acc.org_id, "starter")
    comp = Competitor(organization_id=acc.org_id, name="Кофе Лаб")
    session.add(comp)
    await session.commit()
    await add_posts(session, acc.org_id, role=SourceRole.competitor, topic="кейсы и результаты", n=1, days_ago=20,
                    competitor_id=comp.id)  # история: только текст
    await add_posts(session, acc.org_id, role=SourceRole.competitor, topic="мероприятия", n=1, days_ago=10,
                    competitor_id=comp.id)  # прошлый период, тоже только текст
    cur = await add_posts(session, acc.org_id, role=SourceRole.competitor, topic="кейсы и результаты", n=4,
                          competitor_id=comp.id)
    await add_posts(session, acc.org_id, role=SourceRole.market, topic="мероприятия", n=3)
    await add_posts(session, acc.org_id, role=SourceRole.own, topic="продукт и услуги", n=2)
    await session.execute(update(GlobalPost).where(GlobalPost.global_source_id == cur.global_source_id,
                                                   GlobalPost.external_id == "0").values(overperformance=3.0))
    site = Website(organization_id=acc.org_id, competitor_id=comp.id, url="https://coffee.example.com/",
                   name="Кофе Лаб")
    session.add(site)
    await session.flush()
    page = WebsitePage(website_id=site.id, url="https://coffee.example.com/ceny", kind="pricing", title="Цены")
    session.add(page)
    await session.flush()
    now = datetime.now(UTC)
    session.add_all([
        WebsiteChange(website_id=site.id, page_id=page.id, kind="changed", detected_at=now - timedelta(days=2),
                      summary="Подняли цены на 20%", category="price", importance="high"),
        WebsiteChange(website_id=site.id, page_id=page.id, kind="changed", detected_at=now - timedelta(days=2),
                      summary="Поправили опечатку", category="content", importance="low"),
    ])
    await session.commit()
    return acc


async def make(acc, ai=None, **body) -> dict:
    r = await acc.post("/api/v1/digests/generate", json={"days": 7, **body})
    assert r.status_code == 202, r.text
    await run_job(r.json()["id"], handler(ai))
    return (await acc.get(f"/api/v1/jobs/{r.json()['id']}")).json()


# --- чистые функции ---

def test_next_run():
    after = datetime(2026, 9, 26, 10, 30, tzinfo=UTC)  # суббота
    weekly = DigestSchedule(period="weekly", weekday=0, hour=6)
    assert generator.next_run(weekly, after) == datetime(2026, 9, 28, 6, tzinfo=UTC)
    same_day = DigestSchedule(period="weekly", weekday=5, hour=12)
    assert generator.next_run(same_day, after) == datetime(2026, 9, 26, 12, tzinfo=UTC)
    monthly = DigestSchedule(period="monthly", day=1, hour=6)
    assert generator.next_run(monthly, after) == datetime(2026, 10, 1, 6, tzinfo=UTC)
    dec = DigestSchedule(period="monthly", day=28, hour=6)
    assert generator.next_run(dec, datetime(2026, 12, 29, tzinfo=UTC)) == datetime(2027, 1, 28, 6, tzinfo=UTC)
    custom = DigestSchedule(period="custom", every_days=3, hour=9, last_run_at=datetime(2026, 9, 20, 9, tzinfo=UTC))
    assert generator.next_run(custom, after) == datetime(2026, 9, 29, 9, tzinfo=UTC)
    assert generator.period_days(custom) == 3 and generator.period_days(monthly) == 30


def test_render_escapes_and_falls_back():
    st = {"period": {"from": "2026-09-19T00:00", "to": "2026-09-26T00:00", "days": 7},
          "market": {"posts": 0, "prev_posts": 0, "delta_pct": None, "sources": 0, "median_er": None,
                     "prev_median_er": None, "formats": []},
          "topics": {"rising": [], "new": [],
                     "gaps": [{"topic": "цены", "share_market": 30, "share_own": 0, "gap": 30}]},
          "competitors": [], "other_site_changes": [], "top_posts": [], "outliers": [],
          "own": {"posts": 1, "prev_posts": 0, "delta_pct": None, "median_er": None, "prev_median_er": None,
                  "best_post": None, "audit": None}, "opportunities": []}
    s = render.fallback(st)
    assert s["recommendations"][0]["title"] == "Закрыть пробел: цены" and "Пробелы: цены" in s["topics"]
    cleaned = render.clean({"headline": "<b>Главное</b>", "ideas": [{"title": "Идея", "format": "vk"}]}, st)
    assert cleaned["market"] == s["market"] and cleaned["ideas"][0]["format"] == "vk"
    html = render.email_html("Дайджест <x>", st, cleaned, "https://app/digest/1")
    assert "&lt;b&gt;Главное" in html and "<b>Главное</b>" not in html and "Дайджест &lt;x&gt;" in html
    assert render.markdown("Д", st, cleaned).startswith("# Д\n\n_2026-09-19 — 2026-09-26_")


# --- генерация ---

async def test_digest_with_ai_uses_code_numbers(client, session):
    acc = await world(client, session)
    ai = FakeDigestAI()
    job = await make(acc, ai)
    assert job["status"] == "completed" and job["result"]["ai"] is True, job
    d = (await acc.get(f"/api/v1/digests/{job['result']['digest_id']}")).json()
    st = d["stats"]
    assert st["market"]["posts"] == 7 and st["market"]["prev_posts"] == 1 and st["market"]["delta_pct"] == 600
    comp = st["competitors"][0]
    assert comp["name"] == "Кофе Лаб" and comp["posts"] == 4 and comp["prev_posts"] == 1
    assert comp["new_formats"] == ["photo"]
    assert [c["summary"] for c in comp["site_changes"]] == ["Подняли цены на 20%"]  # мелочь не попадает
    assert [o["overperformance"] for o in st["outliers"]] == [3.0]
    assert "кейсы и результаты" in [t["topic"] for t in st["topics"]["new"]]
    assert st["own"]["posts"] == 2

    s = d["sections"]
    assert d["headline"] == "Конкуренты ушли в кейсы" and s["summary"] == "Итог периода"
    assert s["topics"]  # пустой раздел модели заполнен шаблоном по цифрам
    assert s["recommendations"] == [{"title": "Сделать кейс", "why": "пробел"}]
    assert s["ideas"][0]["format"] == "telegram"
    system, sent = ai.inputs[0]
    assert sent["market"]["posts"] == 7 and "Ниша клиента" in system
    assert d["title"].startswith("Дайджест недельный") and d["trigger"] == "manual"

    items = (await acc.get("/api/v1/digests")).json()
    assert [i["id"] for i in items["items"]] == [d["id"]] and items["job"]["status"] == "completed"
    md = await acc.get(f"/api/v1/digests/{d['id']}/export?type=md")
    assert md.status_code == 200 and "## Конкуренты" in md.text and "Подняли цены на 20%" in md.text
    html = await acc.get(f"/api/v1/digests/{d['id']}/export?type=html")
    assert html.headers["content-type"].startswith("text/html") and f"/digest/{d['id']}" in html.text


async def test_without_key_empty_period_and_model_failure(client, session):
    acc = await world(client, session)
    job = await make(acc)
    assert job["status"] == "completed" and "OPENROUTER_API_KEY" in job["result"]["message"]
    d = (await acc.get(f"/api/v1/digests/{job['result']['digest_id']}")).json()
    assert not d["ai"] and d["sections"]["headline"].startswith("Рынок: 7 публикаций")

    job = await make(acc, FakeDigestAI(fail=AIError("HTTP 500", retryable=False)))
    assert "Модель недоступна" in job["result"]["message"]

    empty = await register(client)
    ai = FakeDigestAI()
    job = await make(empty, ai)
    assert "нет публикаций" in job["result"]["message"] and ai.inputs == []  # на пустых данных модель не зовём


@pytest.fixture
def smtp(monkeypatch):
    sent = []
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "digest@example.com")
    monkeypatch.setattr(mailer, "_send", lambda to, subject, text, html: sent.append((to, subject, text, html)))
    return sent


async def test_email_on_generate_and_resend(client, session, smtp, monkeypatch):
    acc = await world(client, session)
    job = await make(acc, recipients=["Boss@Example.com"])
    assert job["result"]["emailed"] == 1
    to, subject, text, html = smtp[0]
    assert to == ["boss@example.com"] and subject.startswith("Дайджест недельный") and "## Рынок" in text
    assert "<table" in html
    d_id = job["result"]["digest_id"]
    assert (await acc.get(f"/api/v1/digests/{d_id}")).json()["emailed_at"]

    r = await acc.post(f"/api/v1/digests/{d_id}/send", json={"recipients": ["cmo@example.com"]})
    assert r.status_code == 200 and smtp[-1][0] == ["cmo@example.com"]

    def boom(*a):
        raise OSError("connection refused")
    monkeypatch.setattr(mailer, "_send", boom)
    r = await acc.post(f"/api/v1/digests/{d_id}/send", json={"recipients": ["cmo@example.com"]})
    assert r.status_code == 502 and "connection refused" in r.json()["detail"]
    assert "connection refused" in (await acc.get(f"/api/v1/digests/{d_id}")).json()["email_error"]


async def test_email_not_configured(client, session):
    acc = await world(client, session)
    r = await acc.post("/api/v1/digests/generate", json={"recipients": ["a@example.com"]})
    assert r.status_code == 503
    job = await make(acc)
    r = await acc.post(f"/api/v1/digests/{job['result']['digest_id']}/send", json={"recipients": ["a@example.com"]})
    assert r.status_code == 503
    assert (await acc.get("/api/v1/digests/schedule")).json()["email_configured"] is False


async def test_schedule_put_and_due(client, session, smtp):
    acc = await world(client, session)
    default = (await acc.get("/api/v1/digests/schedule")).json()
    assert default["enabled"] is False and default["period"] == "weekly" and default["next_run_at"] is None
    assert (await acc.put("/api/v1/digests/schedule", json={"enabled": True, "send_email": True})).status_code == 422
    r = await acc.put("/api/v1/digests/schedule", json={"enabled": True, "period": "weekly", "weekday": 2, "hour": 7,
                                                        "send_email": True, "recipients": ["Team@Example.com"]})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["recipients"] == ["team@example.com"] and s["email_configured"] is True
    nxt = datetime.fromisoformat(s["next_run_at"])
    assert nxt.weekday() == 2 and nxt.hour == 7 and nxt > datetime.now(UTC)

    member = await _join(client, acc, "member")
    assert (await member.put("/api/v1/digests/schedule", json={})).status_code == 403

    await session.execute(update(DigestSchedule).where(DigestSchedule.organization_id == acc.org_id)
                          .values(next_run_at=datetime.now(UTC) - timedelta(minutes=5)))
    await session.commit()
    assert await generator.schedule_due(session, None) == 1
    assert await generator.schedule_due(session, None) == 0  # уже переназначено
    job = (await session.execute(select(Job).where(Job.organization_id == acc.org_id, Job.kind == "generate_digest")
                                 )).scalar_one()
    assert job.params == {"days": 7, "trigger": "schedule", "recipients": ["team@example.com"]}
    sched = (await acc.get("/api/v1/digests/schedule")).json()
    assert sched["last_run_at"] and datetime.fromisoformat(sched["next_run_at"]) > datetime.now(UTC)

    await session.execute(update(DigestSchedule).where(DigestSchedule.organization_id == acc.org_id)
                          .values(next_run_at=datetime.now(UTC) - timedelta(minutes=5)))
    await session.commit()
    assert await generator.schedule_due(session, None) == 0  # прошлый дайджест ещё в очереди
    await run_job(job.id, handler())
    await session.refresh(job)
    assert job.status == JobStatus.completed and smtp and smtp[-1][0] == ["team@example.com"]


async def test_conflict_roles_and_isolation(client, session):
    acc = await world(client, session)
    other = await register(client)
    assert (await acc.post("/api/v1/digests/generate", json={})).status_code == 202
    assert (await acc.post("/api/v1/digests/generate", json={})).status_code == 409
    assert (await acc.post("/api/v1/digests/generate", json={"days": 0})).status_code == 422
    job_id = (await acc.get("/api/v1/digests")).json()["job"]["id"]
    await run_job(job_id, handler())
    d_id = (await acc.get("/api/v1/digests")).json()["items"][0]["id"]

    assert (await other.get(f"/api/v1/digests/{d_id}")).status_code == 404
    assert (await other.get(f"/api/v1/digests/{d_id}/export")).status_code == 404
    assert (await other.post(f"/api/v1/digests/{d_id}/send", json={"recipients": ["a@example.com"]})
            ).status_code in (404, 503)
    assert (await other.delete(f"/api/v1/digests/{d_id}")).status_code == 404
    assert (await other.get("/api/v1/digests")).json() == {"items": [], "job": None}

    viewer = await _join(client, acc, "viewer")
    assert (await viewer.post("/api/v1/digests/generate", json={})).status_code == 403
    assert (await viewer.delete(f"/api/v1/digests/{d_id}")).status_code == 403
    assert len((await viewer.get("/api/v1/digests")).json()["items"]) == 1
    assert (await acc.delete(f"/api/v1/digests/{d_id}")).status_code == 204
