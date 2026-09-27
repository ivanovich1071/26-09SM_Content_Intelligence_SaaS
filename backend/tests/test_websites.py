import json
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import func, select

from app.ai.openrouter import AIError, AIResult
from app.ai.router import AIRouter
from app.connectors.http import DomainRateLimiter, Fetcher
from app.core.config import settings
from app.models import Competitor, Job, WebsitePage
from app.sources import sync
from app.websites import crawl, diff
from app.websites.discover import classify, parse_sitemap
from app.workers.tasks import run_job
from tests.conftest import register, set_plan
from tests.test_roles import _join

HOME = "https://coffee.example.com/"


def page(title: str, body: str, links: str = "") -> str:
    return f"<html><head><title>{title}</title></head><body><h1>{title}</h1>{body}{links}</body></html>"


class FakeSite:
    def __init__(self):
        self.pages = {
            "/": page("Кофе Лаб", "<p>Лучший кофе в Минске</p>",
                      '<a href="/uslugi">Услуги</a><a href="/about">О нас</a><a href="https://other.com/x">x</a>'),
            "/ceny": page("Цены", "<p>Капучино — 900 BYN за курс бариста</p><p>Эспрессо — 5 BYN</p>"),
            "/uslugi": page("Услуги", "<p>Обучение бариста</p><p>Кейтеринг</p>"),
            "/about": page("О нас", "<p>Мы работаем с 2015 года</p><p>Обновлено 01.09.2026</p>"),
            "/blog": page("Блог", "<p>Статьи</p>"),
            "/blog/first-post": page("Первая статья", "<p>Как мы выбираем зерно</p>"),
            "/private/admin": page("Админка", "<p>секрет</p>"),
        }
        self.sitemap_extra: list[str] = []
        self.calls: list[str] = []

    def sitemap(self) -> str:
        urls = ["/ceny", "/uslugi", "/blog", "/blog/first-post", "/private/admin", "/image.png", *self.sitemap_extra]
        items = "".join(f"<url><loc>https://coffee.example.com{u}</loc><lastmod>2026-09-0{i + 1}</lastmod></url>"
                        for i, u in enumerate(urls))
        return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\nSitemap: https://coffee.example.com/sitemap.xml")
        if path == "/sitemap.xml":
            return httpx.Response(200, text=self.sitemap())
        if path in self.pages:
            return httpx.Response(200, text=self.pages[path])
        return httpx.Response(404, text="нет")


class FakeAnalyst:
    name = "fake"

    def __init__(self, fail: bool = False):
        self.fail, self.calls = fail, []

    async def chat_json(self, model, system, user, *, temperature, max_tokens):
        if self.fail:
            raise AIError("HTTP 500", retryable=False)
        data = json.loads(user)
        self.calls.append(data)
        return AIResult(data={"summary": f"Изменение на странице {data['страница']['тип']}", "category": "price",
                              "importance": "high"}, model=model, provider="fake", usage={"cost": 0.001})


@pytest.fixture
def site(monkeypatch):
    fake = FakeSite()

    @asynccontextmanager
    async def make_fetcher():
        async with Fetcher(DomainRateLimiter(interval=0), transport=httpx.MockTransport(fake),
                           check_hosts=False) as http:
            yield http

    monkeypatch.setattr(sync, "make_fetcher", make_fetcher)
    return fake


async def run_crawl(acc, website_id: int, analyst=None) -> dict:
    from app.core.db import SessionLocal
    async with SessionLocal() as s:
        job = Job(organization_id=acc.org_id, kind="crawl_website", params={"website_id": website_id})
        s.add(job)
        await s.commit()
    await run_job(job.id, lambda s, j: crawl.handle_crawl_website(
        s, j, router=AIRouter(s, analyst, backoff_sec=0) if analyst else None))
    return (await acc.get(f"/api/v1/jobs/{job.id}")).json()


# --- чистые функции ---

@pytest.mark.parametrize("url,kind", [
    ("https://a.by/", "home"), ("https://a.by/ceny", "pricing"), ("https://a.by/pricing/team", "pricing"),
    ("https://a.by/uslugi/kofe", "service"), ("https://a.by/o-nas", "about"), ("https://a.by/blog", "blog"),
    ("https://a.by/blog/kak-vybrat", "article"), ("https://a.by/2026/09/news", "article"),
    ("https://a.by/kontakty", "contacts"), ("https://a.by/random", "other"),
])
def test_classify(url, kind):
    assert classify(url, "https://a.by/") == kind


def test_parse_sitemap_index_and_broken():
    idx = b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://a.by/s1.xml</loc>' \
          b'</sitemap></sitemapindex>'
    assert parse_sitemap(idx) == ([], ["https://a.by/s1.xml"])
    assert parse_sitemap(b"<html>not xml") == ([], [])


def test_meaningful_diff_skips_noise_and_moves():
    old = "Цена 900 BYN\nОбновлено 01.09.2026\n5 минут назад\nБлок А\nБлок Б"
    new = "Цена 1100 BYN\nОбновлено 02.09.2026\n7 минут назад\nБлок Б\nБлок А\n© 2026 Все права защищены"
    added, removed = diff.meaningful_diff(old, new)
    assert added == ["Цена 1100 BYN"] and removed == ["Цена 900 BYN"]


def test_guess_category():
    assert diff.guess("other", ["Капучино 1100 BYN"], []) == ("price", "high")
    assert diff.guess("service", ["Новая услуга: кейтеринг"], []) == ("product", "medium")
    assert diff.guess("home", ["Мы делаем лучший кофе"], []) == ("positioning", "medium")
    assert diff.guess("article", ["Новый абзац"], []) == ("content", "low")


# --- обход ---

async def test_first_crawl_is_baseline_then_changes_detected(client, site):
    acc = await register(client)
    w = (await acc.post("/api/v1/websites", json={"url": "coffee.example.com", "name": "Кофе Лаб"})).json()
    assert w["url"] == HOME and w["last_job"]["status"] == "queued"

    first = await run_crawl(acc, w["id"])
    assert first["status"] == "completed", first
    assert first["result"]["first_crawl"] and first["result"]["changes"] == 0
    pages = {p["url"]: p for p in (await acc.get(f"/api/v1/websites/{w['id']}/pages")).json()}
    assert "https://coffee.example.com/private/admin" not in pages  # robots.txt
    assert "https://coffee.example.com/image.png" not in pages and not any("other.com" in u for u in pages)
    kinds = {u.removeprefix("https://coffee.example.com"): p["kind"] for u, p in pages.items()}
    assert kinds == {"/": "home", "/ceny": "pricing", "/uslugi": "service", "/about": "about", "/blog": "blog",
                     "/blog/first-post": "article"}
    assert pages["https://coffee.example.com/ceny"]["title"] == "Цены"

    # между обходами: подняли цену, добавили статью, удалили услуги, в «О нас» поменялась только дата
    site.pages["/ceny"] = site.pages["/ceny"].replace("900 BYN", "1100 BYN")
    site.pages["/blog/new-roast"] = page("Новая обжарка", "<p>Эфиопия</p>")
    site.sitemap_extra.append("/blog/new-roast")
    del site.pages["/uslugi"]
    site.pages["/about"] = site.pages["/about"].replace("01.09.2026", "20.09.2026")

    analyst = FakeAnalyst()
    second = await run_crawl(acc, w["id"], analyst)
    assert second["status"] == "completed" and second["result"]["changes"] == 3
    changes = (await acc.get(f"/api/v1/websites/changes?website_id={w['id']}")).json()
    assert changes[0]["importance"] == "high"  # важные — первыми
    by_kind = {c["kind"]: c for c in changes}
    assert set(by_kind) == {"changed", "new_page", "removed_page"}
    price = by_kind["changed"]
    assert price["page_kind"] == "pricing" and price["ai"] and price["importance"] == "high"
    assert analyst.calls[0]["удалено"] == ["Капучино — 900 BYN за курс бариста"]
    assert by_kind["new_page"]["page_title"] == "Новая обжарка"
    assert by_kind["removed_page"]["page_url"].endswith("/uslugi") and not by_kind["removed_page"]["ai"]

    d = (await acc.get(f"/api/v1/websites/changes/{price['id']}")).json()
    assert d["added"] == ["Капучино — 1100 BYN за курс бариста"] and "900 BYN" in d["before_text"]
    assert "1100 BYN" in d["after_text"]

    third = await run_crawl(acc, w["id"], analyst)  # ничего не менялось — изменений нет
    assert third["result"]["changes"] == 0
    detail = (await acc.get(f"/api/v1/websites/{w['id']}")).json()
    assert detail["status"] == "ok" and detail["changes_30d"] == 3


async def test_model_failure_falls_back_to_heuristics(client, site):
    acc = await register(client)
    w = (await acc.post("/api/v1/websites", json={"url": HOME})).json()
    await run_crawl(acc, w["id"])
    site.pages["/ceny"] = site.pages["/ceny"].replace("900", "990")
    job = await run_crawl(acc, w["id"], FakeAnalyst(fail=True))
    assert job["status"] == "completed" and "по словам" in job["result"]["message"]
    c = (await acc.get("/api/v1/websites/changes")).json()[0]
    assert c["category"] == "price" and c["importance"] == "high" and not c["ai"]


async def test_page_limit_by_plan(client, site):
    acc = await register(client)
    await set_plan(acc.org_id, "free")  # Free: 10 страниц
    for i in range(15):
        site.pages[f"/blog/post-{i}"] = page(f"Пост {i}", "<p>текст</p>")
        site.sitemap_extra.append(f"/blog/post-{i}")
    w = (await acc.post("/api/v1/websites", json={"url": HOME})).json()
    await run_crawl(acc, w["id"])
    info = (await acc.get(f"/api/v1/websites/{w['id']}")).json()
    assert info["pages_tracked"] == 10 and info["page_limit"] == 10
    pages = (await acc.get(f"/api/v1/websites/{w['id']}/pages")).json()
    tracked_kinds = [p["kind"] for p in pages if p["tracked"]]
    assert {"home", "pricing", "service", "about"} <= set(tracked_kinds)  # важные — первыми

    untracked = next(p for p in pages if not p["tracked"])
    r = await acc.patch(f"/api/v1/websites/{w['id']}/pages/{untracked['id']}", json={"tracked": True})
    assert r.status_code == 402 and r.json()["detail"]["metric"] == "website_pages"
    some = next(p for p in pages if p["tracked"] and p["kind"] == "article")
    assert (await acc.patch(f"/api/v1/websites/{w['id']}/pages/{some['id']}", json={"tracked": False})).json()[
        "tracked"] is False
    assert (await acc.patch(f"/api/v1/websites/{w['id']}/pages/{untracked['id']}", json={"tracked": True})
            ).status_code == 200


async def test_unreachable_site_fails_clearly(client, site):
    acc = await register(client)
    w = (await acc.post("/api/v1/websites", json={"url": "https://coffee.example.com/nope"})).json()
    job = await run_crawl(acc, w["id"])
    assert job["status"] == "failed" and "HTTP 404" in job["error"]
    assert (await acc.get(f"/api/v1/websites/{w['id']}")).json()["status"] == "error"


# --- API ---

async def test_quota_duplicate_competitor_and_roles(client, session, site):
    acc = await register(client)
    assert (await acc.post("/api/v1/websites", json={"url": HOME})).status_code == 201
    r = await acc.post("/api/v1/websites", json={"url": "другой.by"})
    assert r.status_code == 402 and r.json()["detail"]["metric"] == "websites"

    await set_plan(acc.org_id, "professional")
    assert (await acc.post("/api/v1/websites", json={"url": "coffee.example.com/"})).status_code == 409
    comp = Competitor(organization_id=acc.org_id, name="Кофе Лаб")
    session.add(comp)
    await session.commit()
    w = (await acc.post("/api/v1/websites", json={"url": "lab.example.com", "competitor_id": comp.id})).json()
    assert w["competitor_name"] == "Кофе Лаб"
    assert (await acc.post(f"/api/v1/websites/{w['id']}/crawl")).status_code == 409  # первый обход в очереди

    viewer = await _join(client, acc, "viewer")
    assert len((await viewer.get("/api/v1/websites")).json()) == 2
    assert (await viewer.post("/api/v1/websites", json={"url": "x.example.com"})).status_code == 403
    assert (await viewer.delete(f"/api/v1/websites/{w['id']}")).status_code == 403
    assert (await acc.delete(f"/api/v1/websites/{w['id']}")).status_code == 204


async def test_changes_by_competitor_and_isolation(client, session, site):
    a = await register(client, "A")
    await set_plan(a.org_id, "starter")
    comp = Competitor(organization_id=a.org_id, name="Кофе Лаб")
    session.add(comp)
    await session.commit()
    w = (await a.post("/api/v1/websites", json={"url": HOME, "competitor_id": comp.id})).json()
    await run_crawl(a, w["id"])
    site.pages["/ceny"] = site.pages["/ceny"].replace("900", "950")
    await run_crawl(a, w["id"])
    by_comp = (await a.get(f"/api/v1/websites/changes?competitor_id={comp.id}")).json()
    assert len(by_comp) == 1 and by_comp[0]["competitor_name"] == "Кофе Лаб"
    assert (await a.get("/api/v1/websites/changes?importance=low")).json() == []

    b = await register(client, "B")
    assert (await b.get("/api/v1/websites")).json() == [] and (await b.get("/api/v1/websites/changes")).json() == []
    for url in (f"/api/v1/websites/{w['id']}", f"/api/v1/websites/{w['id']}/pages",
                f"/api/v1/websites/changes/{by_comp[0]['id']}"):
        assert (await b.get(url)).status_code == 404, url
    assert (await b.post(f"/api/v1/websites/{w['id']}/crawl")).status_code == 404
    assert (await b.delete(f"/api/v1/websites/{w['id']}")).status_code == 404
    assert (await b.post("/api/v1/websites", json={"url": "b.example.com", "competitor_id": comp.id})
            ).status_code == 404


async def test_schedule_due(client, session, site, monkeypatch):
    acc = await register(client)
    w = (await acc.post("/api/v1/websites", json={"url": HOME})).json()
    await run_crawl(acc, w["id"])

    def count():
        return select(func.count()).select_from(Job).where(Job.organization_id == acc.org_id,
                                                           Job.kind == "crawl_website")
    before = (await session.execute(count())).scalar_one()
    monkeypatch.setattr(settings, "website_crawl_days", 0)
    await crawl.schedule_due(session, None)
    assert (await session.execute(count())).scalar_one() == before  # задача из POST ещё в очереди — не дублируем
    await acc.post(f"/api/v1/jobs/{w['last_job']['id']}/cancel")
    await crawl.schedule_due(session, None)
    await crawl.schedule_due(session, None)
    assert (await session.execute(count())).scalar_one() == before + 1

    _ = WebsitePage
