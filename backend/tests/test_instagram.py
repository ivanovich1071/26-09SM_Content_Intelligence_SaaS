import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from app.connectors import InvalidSource, detect_kind, get_connector
from app.connectors.http import DomainRateLimiter, Fetcher
from app.connectors.instagram import APIFY_URL, map_profile
from app.core.config import settings
from app.workers.tasks import sync_source
from tests.conftest import register

LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)


def apify_item(username: str) -> dict:
    """Синтетический ответ в формате документации актора apify/instagram-profile-scraper (из VM_SM)."""
    return {
        "username": username, "fullName": "ProDigital", "biography": "Школа digital",
        "followersCount": 12000, "postsCount": 900,
        "latestPosts": [
            {"id": "1", "shortCode": "AAA", "type": "Video", "productType": "clips", "caption": "Рилс",
             "url": "https://www.instagram.com/p/AAA/", "timestamp": "2026-09-20T10:00:00.000Z",
             "likesCount": 150, "commentsCount": 12, "videoPlayCount": 5400},
            {"id": "2", "shortCode": "BBB", "type": "Sidecar", "caption": "Карусель",
             "timestamp": "2026-09-18T10:00:00.000Z", "likesCount": -1, "commentsCount": 3},
        ],
    }


class FakeApify:
    def __init__(self, status: int = 200):
        self.status, self.calls = status, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == APIFY_URL and request.method == "POST"
        body = json.loads(request.content)
        self.calls.append({"auth": request.headers.get("authorization"), **body})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "x"})
        return httpx.Response(200, json=[apify_item(u) for u in body["usernames"] if u != "closed_profile"])


def fetcher(fake: FakeApify) -> Fetcher:
    return Fetcher(DomainRateLimiter(interval=0), transport=httpx.MockTransport(fake), check_hosts=False)


def test_map_profile():
    profile, posts = map_profile(apify_item("prodigital.by"))
    assert profile.followers == 12000 and profile.title == "ProDigital" and profile.meta["posts_total"] == 900
    assert posts[0].media_type == "reel" and posts[0].views == 5400 and posts[0].comments == 12
    assert posts[1].media_type == "carousel" and posts[1].likes is None
    assert posts[1].url == "https://www.instagram.com/p/BBB/"
    assert posts[0].published_at == datetime(2026, 9, 20, 10, tzinfo=UTC)


@pytest.mark.parametrize("raw", ["https://www.instagram.com/ProDigital.by/", "instagram.com/prodigital.by?igsh=1",
                                 "@prodigital.by", "prodigital.by"])
def test_normalize(raw):
    assert get_connector("instagram").normalize(raw) == ("prodigital.by", "https://www.instagram.com/prodigital.by/")


@pytest.mark.parametrize("raw", ["https://www.instagram.com/p/AAA/", "https://instagram.com/reel/X1/", "про смм"])
def test_normalize_rejects_posts_and_garbage(raw):
    with pytest.raises(InvalidSource):
        get_connector("instagram").normalize(raw)


def test_detect_kind_instagram():
    assert detect_kind("https://www.instagram.com/prodigital.by/") == "instagram"


async def test_without_token_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "apify_token", "")
    fake = FakeApify()
    async with fetcher(fake) as http:
        profile = await get_connector("instagram").get_profile(http, "x", "", {})
    assert not profile.available and "APIFY_TOKEN" in profile.reason and fake.calls == []


async def test_one_apify_call_per_sync(monkeypatch):
    monkeypatch.setattr(settings, "apify_token", "tok")
    fake = FakeApify()
    ig = get_connector("instagram")
    async with fetcher(fake) as http:
        profile = await ig.get_profile(http, "prodigital.by", "", {})
        result = await ig.collect(http, "prodigital.by", "", {}, since=LONG_AGO, known_ids=set())
    assert profile.available and len(result.items) == 2
    assert fake.calls == [{"auth": "Bearer tok", "usernames": ["prodigital.by"]}]


async def test_closed_profile_and_bad_token(monkeypatch):
    monkeypatch.setattr(settings, "apify_token", "tok")
    ig = get_connector("instagram")
    async with fetcher(FakeApify()) as http:
        assert not (await ig.get_profile(http, "closed_profile", "", {})).available
    from app.connectors.http import FetchError
    async with fetcher(FakeApify(status=401)) as http:
        with pytest.raises(FetchError, match="APIFY_TOKEN"):
            await ig.get_profile(http, "x", "", {})


async def test_instagram_source_sync(client, monkeypatch):
    from contextlib import asynccontextmanager

    from app.sources import sync
    monkeypatch.setattr(settings, "apify_token", "tok")
    monkeypatch.setattr(settings, "source_history_days", 36500)
    fake = FakeApify()

    @asynccontextmanager
    async def make_fetcher():
        async with fetcher(fake) as http:
            yield http

    monkeypatch.setattr(sync, "make_fetcher", make_fetcher)
    acc = await register(client)
    handle = f"brand_{uuid.uuid4().hex[:6]}"
    src = (await acc.post("/api/v1/sources", json={"url": f"https://instagram.com/{handle}", "role": "competitor"}
                          )).json()
    assert src["kind"] == "instagram"
    await sync_source({}, src["last_job"]["id"])
    done = (await acc.get(f"/api/v1/sources/{src['id']}")).json()
    assert done["status"] == "ok" and done["followers"] == 12000 and done["posts_count"] == 2
    assert len(fake.calls) == 1
    reel = next(p for p in (await acc.get(f"/api/v1/sources/{src['id']}/posts")).json() if p["external_id"] == "AAA")
    assert reel["media_type"] == "reel" and reel["views"] == 5400 and reel["comments"] == 12
