import json
import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import func, select

from app.ai.openrouter import AIResult
from app.ai.router import AIRouter
from app.competitors import profile, stats
from app.connectors import detect_kind, get_connector, social_kind
from app.connectors.base import InvalidSource
from app.connectors.http import DomainRateLimiter, Fetcher, FetchError
from app.connectors.vk import to_item
from app.connectors.youtube import parse_feed as parse_yt
from app.core.config import settings
from app.models import Competitor, Source, SourceRole
from app.workers.tasks import run_job
from tests.conftest import register, set_plan
from tests.test_analysis import FakeClassifier, analyze, make_source
from tests.test_roles import _join

LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)

YT_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
 <title>Кофе Лаб</title>
 <entry>
  <id>yt:video:abc123</id><yt:videoId>abc123</yt:videoId><yt:channelId>UCaaaaaaaaaaaaaaaaaaaaaa</yt:channelId>
  <title>Как выбрать зерно</title><link rel="alternate" href="https://www.youtube.com/watch?v=abc123"/>
  <author><name>Кофе Лаб</name></author><published>2026-09-01T10:00:00+00:00</published>
  <media:group><media:description>Разбираем обжарку</media:description>
   <media:community><media:starRating count="42" average="5.00"/><media:statistics views="1500"/></media:community>
  </media:group>
 </entry>
</feed>"""

VK_POST = {"id": 77, "owner_id": -123, "date": 1790000000, "text": "Скидка на обжарку",
           "views": {"count": 900}, "likes": {"count": 30}, "comments": {"count": 4}, "reposts": {"count": 2},
           "attachments": [{"type": "photo"}, {"type": "photo"},
                           {"type": "link", "link": {"url": "https://example.com/promo"}}]}


def fetcher(handler) -> Fetcher:
    return Fetcher(DomainRateLimiter(interval=0), transport=httpx.MockTransport(handler), check_hosts=False)


# --- YouTube ---

@pytest.mark.parametrize("raw,key", [
    ("https://www.youtube.com/@CoffeeLab", "@coffeelab"), ("youtube.com/@coffeelab/videos", "@coffeelab"),
    ("https://m.youtube.com/channel/UCaaaaaaaaaaaaaaaaaaaaaa", "UCaaaaaaaaaaaaaaaaaaaaaa"),
])
def test_youtube_normalize(raw, key):
    assert get_connector("youtube").normalize(raw)[0] == key


def test_youtube_normalize_rejects_video():
    with pytest.raises(InvalidSource):
        get_connector("youtube").normalize("https://www.youtube.com/watch?v=abc")


def test_youtube_parse_feed():
    title, items = parse_yt(YT_FEED)
    v = items[0]
    assert title == "Кофе Лаб" and v.external_id == "abc123" and v.views == 1500 and v.likes == 42
    assert v.title == "Как выбрать зерно" and v.text == "Разбираем обжарку" and v.media_type == "video"


async def test_youtube_handle_resolves_channel_id_once():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/@coffeelab":
            assert "CONSENT" in request.headers.get("cookie", "")
            return httpx.Response(200, text='<script>{"channelId":"UCaaaaaaaaaaaaaaaaaaaaaa"}</script>')
        if request.url.path == "/feeds/videos.xml":
            return httpx.Response(200, text=YT_FEED)
        return httpx.Response(404)

    yt = get_connector("youtube")
    key, url = yt.normalize("https://www.youtube.com/@coffeelab")
    async with fetcher(handler) as http:
        prof = await yt.get_profile(http, key, url, {})
        res = await yt.collect(http, key, url, prof.meta, since=LONG_AGO, known_ids=set())
    assert prof.available and prof.meta == {"channel_id": "UCaaaaaaaaaaaaaaaaaaaaaa"} and prof.title == "Кофе Лаб"
    assert len(res.items) == 1 and len(calls) == 2  # лента взята из кэша задачи, второй раз не качается


# --- VK ---

def test_vk_normalize_and_item():
    assert get_connector("vk").normalize("https://vk.com/Coffee_Lab") == ("coffee_lab", "https://vk.com/coffee_lab")
    with pytest.raises(InvalidSource):
        get_connector("vk").normalize("https://vk.com/feed")
    item = to_item(VK_POST, "coffee_lab")
    assert item.url == "https://vk.com/wall-123_77" and item.media_type == "album"
    assert (item.views, item.likes, item.comments, item.shares) == (900, 30, 4, 2)
    assert item.links == ["https://example.com/promo"]


async def test_vk_uses_token_in_body_not_url(monkeypatch):
    monkeypatch.setattr(settings, "vk_service_token", "secret-token")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        seen.append((str(request.url), form))
        if request.url.path.endswith("groups.getById"):
            return httpx.Response(200, json={"response": {"groups": [
                {"id": 123, "name": "Кофе Лаб", "members_count": 5400, "description": "Обжарка"}]}})
        return httpx.Response(200, json={"response": {"count": 1, "items": [VK_POST]}})

    vk = get_connector("vk")
    async with fetcher(handler) as http:
        prof = await vk.get_profile(http, "coffee_lab", "", {})
        res = await vk.collect(http, "coffee_lab", "", {}, since=LONG_AGO, known_ids=set())
    assert prof.available and prof.followers == 5400 and prof.meta == {"owner_id": -123}
    assert len(res.items) == 1 and res.pages == 1
    assert all("secret-token" not in url for url, _ in seen)
    assert all(form["access_token"] == ["secret-token"] for _, form in seen)


async def test_vk_without_token_and_bad_token(monkeypatch):
    vk = get_connector("vk")
    monkeypatch.setattr(settings, "vk_service_token", "")
    async with fetcher(lambda r: httpx.Response(500)) as http:
        assert "VK_SERVICE_TOKEN" in (await vk.get_profile(http, "x_group", "", {})).reason
    monkeypatch.setattr(settings, "vk_service_token", "bad")
    async with fetcher(lambda r: httpx.Response(200, json={"error": {"error_code": 5, "error_msg": "auth"}})) as http:
        with pytest.raises(FetchError, match="VK_SERVICE_TOKEN"):
            await vk.get_profile(http, "x_group", "", {})


@pytest.mark.parametrize("url,kind", [
    ("https://t.me/coffee", "telegram"), ("https://instagram.com/coffee", "instagram"),
    ("https://www.youtube.com/@coffee", "youtube"), ("https://vk.com/coffee", "vk"),
    ("https://linkedin.com/company/coffee", None), ("https://coffee.by", None),
])
def test_social_kind(url, kind):
    assert social_kind(url) == kind
    assert detect_kind(url) == (kind or "website")


# --- API конкурентов ---

async def starter(client, name="Org"):
    acc = await register(client, name)
    await set_plan(acc.org_id, "starter")  # 3 конкурента, 5 источников
    return acc


def h() -> str:
    return f"c_{uuid.uuid4().hex[:8]}"


async def test_free_plan_has_no_competitors(client):
    acc = await register(client)
    await set_plan(acc.org_id, "free")  # Free: 0 конкурентов
    r = await acc.post("/api/v1/competitors", json={"name": "Кофе Лаб"})
    assert r.status_code == 402 and "конкуренты" in r.json()["detail"]["message"]


async def test_create_with_sources_and_website(client, session):
    acc = await starter(client)
    r = await acc.post("/api/v1/competitors", json={
        "name": "Кофе Лаб", "website": "coffeelab.by", "notes": "главный",
        "sources": [f"https://t.me/{h()}", "https://www.youtube.com/@coffeelab"]})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["website"] == "https://coffeelab.by/" and len(c["sources"]) == 3
    assert {s["kind"] for s in c["sources"]} == {"telegram", "youtube", "website"}
    assert all(s["role"] == "competitor" and s["last_job"]["status"] == "queued" for s in c["sources"])
    assert c["stats"]["posts"] == 0 and c["profile"] is None
    listed = (await acc.get("/api/v1/competitors")).json()
    assert [x["name"] for x in listed] == ["Кофе Лаб"]
    assert (await acc.post("/api/v1/competitors", json={"name": "Кофе Лаб"})).status_code == 409


async def test_sources_quota_is_all_or_nothing(client, session):
    acc = await starter(client)
    r = await acc.post("/api/v1/competitors", json={"name": "Много каналов",
                                                    "sources": [f"@{h()}" for _ in range(6)]})
    assert r.status_code == 402 and r.json()["detail"]["metric"] == "sources"
    assert (await acc.get("/api/v1/competitors")).json() == []
    assert (await acc.get("/api/v1/sources")).json() == []


async def test_existing_source_is_linked_not_duplicated(client):
    acc = await starter(client)
    handle = h()
    market = (await acc.post("/api/v1/sources", json={"url": f"@{handle}", "role": "market"})).json()
    c = (await acc.post("/api/v1/competitors", json={"name": "A", "sources": [f"t.me/{handle}"],
                                                     "track_website": False})).json()
    assert [s["id"] for s in c["sources"]] == [market["id"]] and c["sources"][0]["role"] == "competitor"
    assert len((await acc.get("/api/v1/sources")).json()) == 1

    r = await acc.post("/api/v1/competitors", json={"name": "B", "sources": [f"@{handle}"]})
    assert r.status_code == 409 and "другому конкуренту" in r.json()["detail"]
    assert [x["name"] for x in (await acc.get("/api/v1/competitors")).json()] == ["A"]  # B откатился


async def test_add_source_patch_delete(client, session):
    acc = await starter(client)
    c = (await acc.post("/api/v1/competitors", json={"name": "A"})).json()
    assert c["sources"] == []
    r = await acc.post(f"/api/v1/competitors/{c['id']}/sources", json={"url": "https://vk.com/coffee_lab"})
    assert r.status_code == 201 and r.json()["sources"][0]["kind"] == "vk"
    assert (await acc.post(f"/api/v1/competitors/{c['id']}/sources", json={"url": "vk.com/feed"})).status_code == 422
    r = await acc.patch(f"/api/v1/competitors/{c['id']}", json={"name": "A+", "notes": "сеть кофеен"})
    assert r.json()["name"] == "A+" and r.json()["notes"] == "сеть кофеен"

    assert (await acc.delete(f"/api/v1/competitors/{c['id']}")).status_code == 204
    assert (await acc.get("/api/v1/sources")).json() == []  # источники конкурента удалены вместе с ним


async def test_viewer_and_isolation(client):
    owner = await starter(client, "A")
    c = (await owner.post("/api/v1/competitors", json={"name": "A"})).json()
    viewer = await _join(client, owner, "viewer")
    assert len((await viewer.get("/api/v1/competitors")).json()) == 1
    assert (await viewer.post("/api/v1/competitors", json={"name": "X"})).status_code == 403
    assert (await viewer.delete(f"/api/v1/competitors/{c['id']}")).status_code == 403

    other = await starter(client, "B")
    base = f"/api/v1/competitors/{c['id']}"
    for url in (base, f"{base}/posts"):
        assert (await other.get(url)).status_code == 404
    assert (await other.patch(base, json={"name": "чужой"})).status_code == 404
    assert (await other.post(f"{base}/sources", json={"url": "@abcdef"})).status_code == 404
    assert (await other.post(f"{base}/profile")).status_code == 404
    assert (await other.delete(base)).status_code == 404
    assert (await other.get("/api/v1/competitors")).json() == []


async def test_discover_finds_socials(client, monkeypatch):
    from app.competitors import router as comp_router
    from tests.test_connectors import RSS, SITE

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/blog/rss.xml":
            return httpx.Response(200, text=RSS)
        if request.url.host == "coffee.example.com" and request.url.path == "/":
            return httpx.Response(200, text=SITE.replace("</body>", '<a href="https://vk.com/coffee_lab">vk</a>'
                                                                        '<a href="https://linkedin.com/company/x">in</a>'
                                                                        "</body>"))
        return httpx.Response(404)

    monkeypatch.setattr(comp_router, "Fetcher", lambda: fetcher(handler))
    monkeypatch.setattr(settings, "vk_service_token", "")
    acc = await starter(client)
    r = await acc.post("/api/v1/competitors/discover", json={"website": "coffee.example.com"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["title"] == "Компания" and d["feed_url"].endswith("/blog/rss.xml")
    links = {x["url"]: x for x in d["social_links"]}
    assert links["https://t.me/neuronika_ai"]["kind"] == "telegram"
    assert links["https://vk.com/coffee_lab"]["needs_key"] is True
    assert links["https://linkedin.com/company/x"]["supported"] is False


# --- статистика и профиль ---

async def competitor_with_posts(acc, session, n: int = 6) -> Competitor:
    c = Competitor(organization_id=acc.org_id, name=f"К-{uuid.uuid4().hex[:6]}")
    session.add(c)
    await session.commit()
    src = await make_source(session, acc.org_id, [{"views": 100 * (i + 1), "likes": 5 + i} for i in range(n)])
    src.competitor_id, src.role = c.id, SourceRole.competitor
    await session.commit()
    await analyze(acc, session, src.id, provider=FakeClassifier(topic="акции и предложения"))
    return c


async def test_stats_computed_by_code(client, session):
    acc = await starter(client)
    c = await competitor_with_posts(acc, session)
    detail = (await acc.get(f"/api/v1/competitors/{c.id}?days=90")).json()
    st = detail["stats"]
    assert st["posts"] == 6 and st["analyzed"] == 6 and st["posts_per_week"] == round(6 / (90 / 7), 1)
    assert st["topics"][0] == {"value": "акции и предложения", "count": 6, "share": 100.0}
    assert st["cta_share"] == 0.0 and st["case_share"] == 100.0 and st["median_views"] == 350
    assert len(st["weekly"]) == stats.WEEKS and sum(w["posts"] for w in st["weekly"]) == 6
    top = (await acc.get(f"/api/v1/competitors/{c.id}/posts?sort=top")).json()
    assert top[0]["views"] == 600 and top[0]["analysis"]["topic"] == "акции и предложения"


class FakeAnalyst:
    name = "fake"

    def __init__(self):
        self.user = None

    async def chat_json(self, model, system, user, *, temperature, max_tokens):
        self.user = json.loads(user)
        return AIResult(data={"summary": "Сеть кофеен, пишет про акции", "positioning": "доступный кофе",
                              "main_topics": ["акции"], "strengths": ["регулярность"], "weaknesses": ["нет кейсов"],
                              "opportunities": ["показать обжарку"]},
                        model=model, provider="fake", usage={"cost": 0.01})


async def test_profile_built_from_stats_and_samples(client, session):
    acc = await starter(client)
    c = await competitor_with_posts(acc, session)
    analyst = FakeAnalyst()
    job = await profile.start(session, None, acc.org_id, c.id)
    await run_job(job.id, lambda s, j: profile.handle_profile_competitor(s, j, router=AIRouter(s, analyst,
                                                                                                backoff_sec=0)))
    d = (await acc.get(f"/api/v1/competitors/{c.id}")).json()
    assert d["profile_job"]["status"] == "completed" and d["profile_at"]
    assert d["profile"]["ai"]["positioning"] == "доступный кофе" and d["profile"]["ai"]["formats"] == []
    assert d["profile"]["stats"]["posts"] == 6
    assert analyst.user["статистика"]["posts"] == 6 and len(analyst.user["примеры_публикаций"]) == 6
    assert analyst.user["примеры_публикаций"][0]["overperformance"] >= analyst.user["примеры_публикаций"][1][
        "overperformance"]


async def test_profile_needs_enough_posts(client, session):
    acc = await starter(client)
    c = Competitor(organization_id=acc.org_id, name="Пустой")
    session.add(c)
    await session.commit()
    job = await profile.start(session, None, acc.org_id, c.id)
    await run_job(job.id, lambda s, j: profile.handle_profile_competitor(s, j, router=AIRouter(s, FakeAnalyst())))
    d = (await acc.get(f"/api/v1/competitors/{c.id}")).json()
    assert d["profile_job"]["status"] == "failed" and d["profile_job"]["error"].startswith("Недостаточно данных")


async def test_first_profile_starts_after_analysis(client, session):
    acc = await starter(client)
    c = await competitor_with_posts(acc, session)
    src_id = (await session.execute(select(Source.id).where(Source.competitor_id == c.id))).scalar_one()
    from app.models import Job
    analysis_job = (await session.execute(select(Job).where(
        Job.kind == "analyze_source", Job.params["source_id"].as_integer() == src_id))).scalars().first()
    await profile.after_analysis(analysis_job.id, None)
    count = lambda: select(func.count()).select_from(Job).where(  # noqa: E731
        Job.kind == "profile_competitor", Job.params["competitor_id"].as_integer() == c.id)
    assert (await session.execute(count())).scalar_one() == 1
    await profile.after_analysis(analysis_job.id, None)  # уже в очереди — не дублируется
    assert (await session.execute(count())).scalar_one() == 1


async def test_refresh_profile_endpoint(client, monkeypatch):
    acc = await starter(client)
    c = (await acc.post("/api/v1/competitors", json={"name": "A"})).json()
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    assert (await acc.post(f"/api/v1/competitors/{c['id']}/profile")).status_code == 503
    monkeypatch.setattr(settings, "openrouter_api_key", "key")
    r = await acc.post(f"/api/v1/competitors/{c['id']}/profile")
    assert r.status_code == 202 and r.json()["profile_job"]["status"] == "queued"
    assert (await acc.post(f"/api/v1/competitors/{c['id']}/profile")).status_code == 409


async def test_competitor_sources_sync_through_fake_web(client, web):  # noqa: F811
    from app.workers.tasks import sync_source
    acc = await starter(client)
    c = (await acc.post("/api/v1/competitors", json={"name": "TG", "sources": [f"@{h()}"]})).json()
    await sync_source({}, c["sources"][0]["last_job"]["id"])
    d = (await acc.get(f"/api/v1/competitors/{c['id']}?days=3650")).json()
    assert d["sources"][0]["status"] == "ok" and d["stats"]["posts"] == 9


from tests.test_sources import web  # noqa: E402, F401 — фикстура сети с фикстурами VM_SM

