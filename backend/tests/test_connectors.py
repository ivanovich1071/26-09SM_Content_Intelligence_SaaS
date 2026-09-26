import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.connectors import InvalidSource, detect_kind, get_connector
from app.connectors.base import canonical_url, content_hash
from app.connectors.http import DomainRateLimiter, Fetcher, FetchError, ensure_public_host
from app.connectors.rss import FeedError
from app.connectors.rss import parse_feed as parse_rss
from app.connectors.telegram import parse_channel_page, parse_count, parse_feed
from app.connectors.website import diff_lines, extract_social, extract_text, page_meta

FIX = Path(__file__).parent / "fixtures"
LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)

CHANNEL_PAGE = ('<div class="tgme_page_title"><span>Нейроника</span></div>'
                '<div class="tgme_page_description">Про ИИ</div><div class="tgme_page_extra">1 394 subscribers</div>')

RSS = """<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>Блог</title><description>Статьи</description>
<item><title>Первая</title><link>https://blog.example.com/1?utm_source=rss</link><guid>p1</guid>
<pubDate>Mon, 06 Jul 2026 08:00:00 +0000</pubDate>
<content:encoded><![CDATA[<p>Текст <b>один</b></p>]]></content:encoded>
<category>ИИ</category></item>
<item><title>Вторая</title><link>https://blog.example.com/2</link><description>Два</description>
<pubDate>2026-07-07T10:00:00</pubDate></item>
<item><title>Дубль</title><link>https://blog.example.com/1</link><guid>p1</guid></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<title>Atom-блог</title><subtitle>Под</subtitle>
<entry><title>Запись</title><id>urn:1</id><link rel="alternate" href="https://a.example.com/1"/>
<published>2026-07-01T00:00:00Z</published><summary>Кратко</summary><author><name>Автор</name></author></entry>
</feed>"""

SITE = """<html><head><title>Компания</title><meta name="description" content="Обучение ИИ">
<link rel="alternate" type="application/rss+xml" href="/blog/rss.xml"></head><body>
<script>var x = 1;</script><h1>Корпоративное обучение ИИ</h1><p>Цена от 900 BYN</p>
<a href="https://t.me/neuronika_AI">tg</a><a href="https://www.instagram.com/egordmitriev/">ig</a>
<iframe src="https://www.youtube.com/embed/QpsqDViCyRA"></iframe></body></html>"""


def fetcher(routes: dict[str, httpx.Response | str], calls: list[str] | None = None) -> Fetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if calls is not None:
            calls.append(url)
        found = routes.get(url)
        if found is None:
            return httpx.Response(404, text="nope")
        return found if isinstance(found, httpx.Response) else httpx.Response(200, text=found)

    return Fetcher(DomainRateLimiter(interval=0), transport=httpx.MockTransport(handler), check_hosts=False)


# --- Telegram (фикстуры и проверки перенесены из VM_SM) ---

def test_parse_count():
    assert parse_count("509") == 509
    assert parse_count("1.39K") == 1390
    assert parse_count("2M") == 2_000_000
    assert parse_count("1 394") == 1394
    assert parse_count("") is None
    assert parse_count("1.2.3") is None


def test_parse_feed_neuronika():
    posts, before = parse_feed((FIX / "tg_s_neuronika_AI.html").read_text(encoding="utf-8"), "neuronika_AI")
    assert len(posts) == 5 and before == 699
    first = posts[0]
    assert first["external_id"] == "699"
    assert first["url"] == "https://t.me/neuronika_AI/699"
    assert first["published_at"] == "2026-07-06T08:56:17+00:00"
    assert first["views"] == 509 and first["format"] == "album"
    assert first["text"].startswith("AI-") and first["likes"] > 0
    assert all(p["published_at"] for p in posts)


def test_parse_feed_last_page_has_no_cursor():
    posts, before = parse_feed((FIX / "tg_s_fsby_news.html").read_text(encoding="utf-8"), "fsby_news")
    assert len(posts) == 4 and before is None


def test_parse_channel_page_personal_vs_channel():
    info = parse_channel_page('<div class="tgme_page_title"><span>ВайбМайнд</span></div><div class="tgme_page_extra">')
    assert info["is_channel"] is False and info["title"] == "ВайбМайнд"
    info = parse_channel_page(CHANNEL_PAGE)
    assert info["is_channel"] is True and info["followers"] == 1394


@pytest.mark.parametrize("raw", ["@neuronika_AI", "neuronika_AI", "https://t.me/neuronika_AI",
                                 "t.me/s/neuronika_AI", "https://telegram.me/neuronika_AI/699"])
def test_telegram_normalize(raw):
    assert get_connector("telegram").normalize(raw) == ("neuronika_ai", "https://t.me/neuronika_AI")


@pytest.mark.parametrize("raw", ["https://t.me/+AbCdEf", "t.me/joinchat/xyz", "@ab", "про ИИ"])
def test_telegram_normalize_rejects(raw):
    with pytest.raises(InvalidSource):
        get_connector("telegram").normalize(raw)


async def test_telegram_collect_paginates_until_cursor_ends():
    feed = (FIX / "tg_s_neuronika_AI.html").read_text(encoding="utf-8")
    older = (FIX / "tg_s_fsby_news.html").read_text(encoding="utf-8")
    calls: list[str] = []
    tg = get_connector("telegram")
    async with fetcher({"https://t.me/neuronika_ai": CHANNEL_PAGE, "https://t.me/s/neuronika_ai": feed,
                        "https://t.me/s/neuronika_ai?before=699": older}, calls) as http:
        profile = await tg.get_profile(http, "neuronika_ai", "", {})
        result = await tg.collect(http, "neuronika_ai", "", {}, since=LONG_AGO, known_ids=set())
    assert profile.available and profile.followers == 1394 and profile.title == "Нейроника"
    assert result.pages == 2 and len(result.items) == 9
    assert calls[-1].endswith("?before=699")
    item = next(i for i in result.items if i.external_id == "699")
    assert item.published_at == datetime(2026, 7, 6, 8, 56, 17, tzinfo=UTC) and item.views == 509


async def test_telegram_collect_stops_at_known_posts_and_since():
    feed = (FIX / "tg_s_neuronika_AI.html").read_text(encoding="utf-8")
    tg = get_connector("telegram")
    known = {p["external_id"] for p in parse_feed(feed, "neuronika_AI")[0]}
    calls: list[str] = []
    async with fetcher({"https://t.me/s/x_channel": feed}, calls) as http:
        result = await tg.collect(http, "x_channel", "", {}, since=LONG_AGO, known_ids=known)
        assert result.pages == 1 and len(result.items) == 5  # метрики известных постов обновляются
        recent = await tg.collect(http, "x_channel", "", {}, since=datetime(2026, 7, 16, tzinfo=UTC),
                                  known_ids=set())
    assert len(calls) == 2
    assert all(i.published_at >= datetime(2026, 7, 16, tzinfo=UTC) for i in recent.items)


async def test_telegram_personal_account_unavailable():
    async with fetcher({"https://t.me/someone": '<div class="tgme_page_title">Иван</div>'}) as http:
        profile = await get_connector("telegram").get_profile(http, "someone", "", {})
    assert not profile.available and "не публичный канал" in profile.reason


# --- RSS / Atom ---

def test_parse_rss():
    feed, items = parse_rss(RSS)
    assert feed == {"title": "Блог", "description": "Статьи"}
    assert [i.external_id for i in items] == ["p1", "https://blog.example.com/2", "p1"]
    first = items[0]
    assert first.text == "Текст один" and first.raw_payload["categories"] == ["ИИ"]
    assert first.published_at == datetime(2026, 7, 6, 8, tzinfo=UTC)
    assert first.canonical_url == "https://blog.example.com/1"
    assert items[1].published_at.tzinfo is not None  # дата без пояса → UTC


def test_parse_atom():
    feed, items = parse_rss(ATOM)
    assert feed["title"] == "Atom-блог"
    assert items[0].url == "https://a.example.com/1" and items[0].author == "Автор" and items[0].text == "Кратко"


@pytest.mark.parametrize("bad", ["<html><body>не лента</body></html>", "не xml",
                                 '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss>&a;</rss>'])
def test_parse_rss_rejects(bad):
    with pytest.raises(FeedError):
        parse_rss(bad)


# --- Сайт (проверки перенесены из VM_SM site_watch) ---

def test_extract_text_drops_scripts():
    text = extract_text(SITE)
    assert "Корпоративное обучение ИИ" in text and "var x" not in text


def test_extract_social_normalizes_and_skips_embeds():
    assert extract_social(SITE) == ["https://instagram.com/egordmitriev", "https://t.me/neuronika_ai"]


def test_diff_lines():
    added, removed = diff_lines("a\nЦена от 900 BYN\nc", "a\nЦена от 1100 BYN\nc")
    assert added == ["Цена от 1100 BYN"] and removed == ["Цена от 900 BYN"]


def test_page_meta_finds_feed():
    meta = page_meta(SITE, "https://example.com/")
    assert meta == {"title": "Компания", "description": "Обучение ИИ", "feeds": ["https://example.com/blog/rss.xml"]}


async def test_website_profile_discovers_feed_and_collects():
    site = get_connector("website")
    key, url = site.normalize("www.Example.com")
    assert (key, url) == ("example.com", "https://www.example.com/")
    async with fetcher({url: SITE, "https://www.example.com/blog/rss.xml": RSS}) as http:
        profile = await site.get_profile(http, key, url, {})
        result = await site.collect(http, key, url, profile.meta, since=LONG_AGO, known_ids=set())
    assert profile.available and profile.meta["feed_url"] == "https://www.example.com/blog/rss.xml"
    assert "https://t.me/neuronika_ai" in profile.meta["social_links"]
    assert len(result.items) == 3


async def test_website_without_feed_collects_nothing():
    site = get_connector("website")
    async with fetcher({"https://plain.example.com/": "<html><title>Без блога</title></html>"}) as http:
        profile = await site.get_profile(http, "plain.example.com", "https://plain.example.com/", {})
        result = await site.collect(http, "", "", profile.meta, since=LONG_AGO, known_ids=set())
    assert profile.available and profile.meta["feed_url"] is None and result.items == []


# --- Общее ---

@pytest.mark.parametrize("raw,kind", [
    ("@channel", "telegram"), ("https://t.me/channel", "telegram"), ("example.com", "website"),
    ("https://example.com/feed", "rss"), ("https://example.com/blog/rss.xml", "rss"),
    ("https://example.com/feedback", "website"),
])
def test_detect_kind(raw, kind):
    assert detect_kind(raw) == kind


def test_canonical_url_and_hash():
    assert canonical_url("http://WWW.Example.com/a/?utm_source=x&b=2&a=1#top") == "https://example.com/a?a=1&b=2"
    assert content_hash("Привет,   МИР\n") == content_hash("привет, мир")


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://10.0.0.5/feed", "http://169.254.169.254/latest",
                                 "http://[::1]/", "file:///etc/passwd", "http://192.168.1.1:8080/"])
async def test_private_hosts_blocked(url):
    with pytest.raises(FetchError):
        await ensure_public_host(url)


async def test_redirect_to_private_host_blocked():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

    async with Fetcher(DomainRateLimiter(interval=0), transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(FetchError, match="внутренняя сеть"):
            await http.get("http://1.1.1.1/")


async def test_rate_limiter_spaces_requests_per_domain():
    limiter = DomainRateLimiter(interval=0.2)
    start = time.monotonic()
    for _ in range(3):
        await limiter.wait("t.me")
    await limiter.wait("example.com")  # другой домен не ждёт
    assert 0.35 <= time.monotonic() - start < 1.0
