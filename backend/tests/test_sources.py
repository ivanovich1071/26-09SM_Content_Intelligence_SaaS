import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.connectors.http import DomainRateLimiter, Fetcher
from app.core.config import settings
from app.models import GlobalSource, Job, PostMetric, Source
from app.sources import sync
from app.workers.tasks import sync_source
from tests.conftest import register, set_plan
from tests.test_connectors import CHANNEL_PAGE, RSS, SITE
from tests.test_roles import _join

FIX = Path(__file__).parent / "fixtures"
FEED = (FIX / "tg_s_neuronika_AI.html").read_text(encoding="utf-8")
OLDER = (FIX / "tg_s_fsby_news.html").read_text(encoding="utf-8")


def handle() -> str:
    return f"chan_{uuid.uuid4().hex[:8]}"


class FakeWeb:
    """Интернет для задач синхронизации: любой t.me-канал отдаёт фикстуры VM_SM."""

    def __init__(self):
        self.calls: list[str] = []
        self.views = 509
        self.down = False
        self.personal: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        if self.down:
            raise httpx.ConnectError("offline")
        if request.url.host == "t.me":
            path = request.url.path.strip("/")
            if path.startswith("s/"):
                if "before=" in url:
                    return httpx.Response(200, text=OLDER)
                return httpx.Response(200, text=FEED.replace(">509<", f">{self.views}<"))
            if path in self.personal:
                return httpx.Response(200, text='<div class="tgme_page_title">Иван</div>')
            return httpx.Response(200, text=CHANNEL_PAGE)
        if url.endswith("/blog/rss.xml"):
            return httpx.Response(200, text=RSS)
        if request.url.host.endswith("example.com"):
            return httpx.Response(200, text=SITE)
        return httpx.Response(404)


@pytest.fixture
def web(monkeypatch):
    fake = FakeWeb()

    @asynccontextmanager
    async def make_fetcher():
        async with Fetcher(DomainRateLimiter(interval=0), transport=httpx.MockTransport(fake),
                           check_hosts=False) as http:
            yield http

    monkeypatch.setattr(sync, "make_fetcher", make_fetcher)
    monkeypatch.setattr(settings, "source_history_days", 36500)  # фикстуры датированы 2026 годом
    return fake


async def age_source(session, source_id: int, *, hours: int = 0, days: int = 0) -> None:
    gs_id = (await session.get(Source, source_id)).global_source_id
    await session.execute(GlobalSource.__table__.update().where(GlobalSource.id == gs_id)
                          .values(last_synced_at=func.now() - func.make_interval(0, 0, 0, days, hours)))
    await session.commit()


async def test_add_telegram_source_syncs_posts_with_metrics(client, session, web):
    acc = await register(client)
    h = handle()
    r = await acc.post("/api/v1/sources", json={"url": f"https://t.me/{h}", "role": "competitor"})
    assert r.status_code == 201, r.text
    src = r.json()
    assert src["kind"] == "telegram" and src["key"] == h and src["status"] == "new"
    assert src["last_job"]["status"] == "queued"

    await sync_source({}, src["last_job"]["id"])
    done = (await acc.get(f"/api/v1/sources/{src['id']}/status")).json()
    assert done["status"] == "ok" and done["followers"] == 1394 and done["title"] == "Нейроника"
    assert done["posts_count"] == 9 and done["last_synced_at"]
    job = done["last_job"]
    assert job["status"] == "completed" and job["result"]["posts_new"] == 9 and job["result"]["pages"] == 2

    posts = (await acc.get(f"/api/v1/sources/{src['id']}/posts")).json()
    assert len(posts) == 9 and posts[0]["published_at"] >= posts[-1]["published_at"]
    p699 = next(p for p in posts if p["external_id"] == "699")
    assert p699["views"] == 509 and p699["media_type"] == "album" and p699["url"].endswith("/699")
    snapshots = (await session.execute(select(func.count()).select_from(PostMetric)
                                       .where(PostMetric.post_id == p699["id"]))).scalar_one()
    assert snapshots == 1


async def test_resync_updates_metrics_without_duplicates(client, session, web):
    acc = await register(client)
    src = (await acc.post("/api/v1/sources", json={"url": f"@{handle()}"})).json()
    await sync_source({}, src["last_job"]["id"])

    web.views = 777
    job = (await acc.post(f"/api/v1/sources/{src['id']}/sync")).json()["last_job"]
    await age_source(session, src["id"], hours=1)  # прошло больше source_fresh_minutes

    await sync_source({}, job["id"])
    done = (await acc.get(f"/api/v1/sources/{src['id']}/status")).json()
    assert done["posts_count"] == 9
    assert done["last_job"]["result"]["posts_new"] == 0 and done["last_job"]["result"]["posts_updated"] >= 5
    p699 = next(p for p in (await acc.get(f"/api/v1/sources/{src['id']}/posts")).json() if p["external_id"] == "699")
    assert p699["views"] == 777
    snapshots = (await session.execute(select(PostMetric.views).where(PostMetric.post_id == p699["id"])
                                       .order_by(PostMetric.id))).scalars().all()
    assert snapshots == [509, 777]


async def test_shared_channel_is_collected_once(client, web):
    h = handle()
    a = await register(client, "A")
    b = await register(client, "B")
    src_a = (await a.post("/api/v1/sources", json={"url": f"t.me/{h}"})).json()
    await sync_source({}, src_a["last_job"]["id"])
    fetched = len(web.calls)

    src_b = (await b.post("/api/v1/sources", json={"url": f"https://t.me/s/{h}"})).json()
    assert src_b["posts_count"] == 9  # данные уже есть — клиент B видит их сразу
    await sync_source({}, src_b["last_job"]["id"])
    assert len(web.calls) == fetched
    job = (await b.get(f"/api/v1/sources/{src_b['id']}")).json()["last_job"]
    assert job["status"] == "completed" and job["result"]["skipped"] is True


async def test_not_a_channel_fails_with_reason(client, web):
    acc = await register(client)
    h = handle()
    web.personal.add(h)
    src = (await acc.post("/api/v1/sources", json={"url": f"@{h}"})).json()
    await sync_source({}, src["last_job"]["id"])
    done = (await acc.get(f"/api/v1/sources/{src['id']}")).json()
    assert done["status"] == "unavailable" and "не публичный канал" in done["last_error"]
    assert done["last_job"]["status"] == "failed" and "не публичный канал" in done["last_job"]["error"]


async def test_network_error_marks_source_error(client, web):
    acc = await register(client)
    src = (await acc.post("/api/v1/sources", json={"url": f"@{handle()}"})).json()
    web.down = True
    await sync_source({}, src["last_job"]["id"])
    done = (await acc.get(f"/api/v1/sources/{src['id']}")).json()
    assert done["status"] == "error" and "Ошибка сети" in done["last_error"]
    assert done["last_job"]["status"] == "failed" and done["last_job"]["error"].startswith("Ошибка сети")


async def test_website_source_collects_blog_via_feed(client, web):
    acc = await register(client)
    src = (await acc.post("/api/v1/sources", json={"url": f"{uuid.uuid4().hex[:6]}.example.com", "role": "own"}
                          )).json()
    assert src["kind"] == "website" and src["role"] == "own"
    await sync_source({}, src["last_job"]["id"])
    done = (await acc.get(f"/api/v1/sources/{src['id']}")).json()
    assert done["status"] == "ok" and done["posts_count"] == 2  # дубль guid в ленте схлопнут
    assert done["meta"]["feed_url"].endswith("/blog/rss.xml")
    assert "https://t.me/neuronika_ai" in done["meta"]["social_links"]


async def test_rss_source(client, web):
    acc = await register(client)
    src = (await acc.post("/api/v1/sources", json={"url": f"https://{uuid.uuid4().hex[:6]}.example.com/blog/rss.xml"}
                          )).json()
    assert src["kind"] == "rss"
    await sync_source({}, src["last_job"]["id"])
    done = (await acc.get(f"/api/v1/sources/{src['id']}")).json()
    assert done["status"] == "ok" and done["title"] == "Блог" and done["posts_count"] == 2


async def test_validation_duplicate_and_quota(client):
    acc = await register(client)  # Free: 1 источник
    assert (await acc.post("/api/v1/sources", json={"url": "https://t.me/+secret"})).status_code == 422
    assert (await acc.post("/api/v1/sources", json={"url": "про маркетинг", "kind": "telegram"})).status_code == 422
    h = handle()
    assert (await acc.post("/api/v1/sources", json={"url": f"@{h}"})).status_code == 201
    r = await acc.post("/api/v1/sources", json={"url": f"@{handle()}"})
    assert r.status_code == 402 and "источники" in r.json()["detail"]["message"]

    await set_plan(acc.org_id, "starter")
    dup = await acc.post("/api/v1/sources", json={"url": f"https://t.me/{h.upper()}"})
    assert dup.status_code == 409


async def test_sync_conflict_patch_and_delete(client):
    acc = await register(client)
    src = (await acc.post("/api/v1/sources", json={"url": f"@{handle()}"})).json()
    assert (await acc.post(f"/api/v1/sources/{src['id']}/sync")).status_code == 409  # первый sync ещё в очереди

    r = await acc.patch(f"/api/v1/sources/{src['id']}", json={"role": "competitor", "name": "Главный конкурент"})
    assert r.json()["role"] == "competitor" and r.json()["name"] == "Главный конкурент"
    assert (await acc.patch(f"/api/v1/sources/{src['id']}", json={"enabled": False})).json()["enabled"] is False

    assert (await acc.delete(f"/api/v1/sources/{src['id']}")).status_code == 204
    assert (await acc.get("/api/v1/sources")).json() == []
    assert (await acc.post("/api/v1/sources", json={"url": f"@{handle()}"})).status_code == 201  # квота освободилась


async def test_viewer_reads_but_cannot_change(client):
    owner = await register(client)
    await set_plan(owner.org_id, "starter")
    src = (await owner.post("/api/v1/sources", json={"url": f"@{handle()}"})).json()
    viewer = await _join(client, owner, "viewer")
    assert len((await viewer.get("/api/v1/sources")).json()) == 1
    assert (await viewer.post("/api/v1/sources", json={"url": f"@{handle()}"})).status_code == 403
    assert (await viewer.post(f"/api/v1/sources/{src['id']}/sync")).status_code == 403
    assert (await viewer.delete(f"/api/v1/sources/{src['id']}")).status_code == 403


async def test_schedule_due_creates_one_job_per_global_source(client, session, web):
    h = handle()
    a = await register(client, "A")
    b = await register(client, "B")
    src_a = (await a.post("/api/v1/sources", json={"url": f"@{h}"})).json()
    src_b = (await b.post("/api/v1/sources", json={"url": f"@{h}"})).json()
    await sync_source({}, src_a["last_job"]["id"])
    await sync_source({}, src_b["last_job"]["id"])

    def jobs_for_h():
        return select(func.count()).select_from(Job).where(
            Job.kind == "sync_source", Job.params["source_id"].as_integer().in_([src_a["id"], src_b["id"]]))

    before = (await session.execute(jobs_for_h())).scalar_one()
    await sync.schedule_due(session, None)
    assert (await session.execute(jobs_for_h())).scalar_one() == before  # собран сегодня — рано

    await age_source(session, src_a["id"], days=2)
    await sync.schedule_due(session, None)
    await sync.schedule_due(session, None)  # повторный запуск cron не дублирует задачу в очереди
    assert (await session.execute(jobs_for_h())).scalar_one() == before + 1
