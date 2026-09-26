"""Задача crawl_website: страницы → снимки → изменения «было/стало» → смысл изменения.

Первый обход — только базовые снимки. Дальше: новые страницы (статьи, услуги, цены), изменившиеся и пропавшие.
Смысл изменения даёт модель (задача analyze), без неё — эвристика по словам и типу страницы."""
import hashlib
import json
from datetime import timedelta

from pydantic import BaseModel
from selectolax.parser import HTMLParser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import prompts
from app.ai.openrouter import AIError
from app.ai.router import AIRouter
from app.analysis import taxonomy
from app.billing import quotas
from app.billing.quotas import QuotaExceeded
from app.connectors.http import Fetcher, FetchError
from app.connectors.website import extract_text
from app.core.config import settings
from app.core.db import utcnow
from app.jobs.service import create_job, enqueue, set_status
from app.models import Competitor, Job, JobStatus, PageSnapshot, Website, WebsiteChange, WebsitePage
from app.sources import sync
from app.websites import diff
from app.websites.discover import discover, normalize

DEFAULT_PAGE_LIMIT = 200  # тариф без ограничения
AI_CHANGES_PER_CRAWL = 20
CATEGORIES = {"price", "offer", "product", "positioning", "contacts", "content", "other"}
NEW_PAGE_KINDS = {"article", "blog", "pricing", "service"}  # о каких новых страницах сообщать
ACTIVE = (JobStatus.queued, JobStatus.running, JobStatus.collecting, JobStatus.analyzing)


class CrawlError(Exception):
    user_facing = True


class Meaning(BaseModel):
    summary: str
    category: str = "other"
    importance: str = "low"


def title_of(html: str) -> str | None:
    node = HTMLParser(html).css_first("title")
    return node.text(strip=True)[:500] if node else None


async def page_limit(session: AsyncSession, org_id: int) -> int:
    _, limits = await quotas.plan_limits(session, org_id)
    return limits.get("website_pages") or DEFAULT_PAGE_LIMIT


async def _snapshot(session: AsyncSession, page: WebsitePage, html: str) -> tuple[PageSnapshot | None, PageSnapshot]:
    """→ (предыдущий снимок, текущий). Новый снимок пишется только если текст изменился."""
    text = extract_text(html)[:settings.website_text_max_chars]
    digest = hashlib.sha256(text.encode()).hexdigest()
    prev = (await session.execute(select(PageSnapshot).where(PageSnapshot.page_id == page.id)
                                  .order_by(PageSnapshot.id.desc()).limit(1))).scalar_one_or_none()
    page.title = title_of(html) or page.title
    if prev is not None and prev.text_hash == digest:
        return prev, prev
    snap = PageSnapshot(page_id=page.id, text_hash=digest, title=page.title, text=text)
    session.add(snap)
    await session.flush()
    page.last_hash = digest
    return prev, snap


async def _explain(router: AIRouter | None, niche: str, org_id: int, job_id: int, page: WebsitePage,
                   change: WebsiteChange, company: str) -> None:
    category, importance = diff.guess(page.kind, change.added, change.removed)
    change.category, change.importance = category, importance
    if change.kind == "new_page":
        change.summary = f"Новая страница ({page.kind}): {page.title or page.url}"
    elif change.kind == "removed_page":
        change.summary = f"Страница удалена: {page.title or page.url}"
    if router is None or change.kind == "removed_page":
        return
    user = json.dumps({"компания": company, "страница": {"тип": page.kind, "адрес": page.url, "заголовок": page.title},
                       "удалено": change.removed[:60], "добавлено": change.added[:60]}, ensure_ascii=False)
    data = await router.run("analyze", prompts.load("website/change").replace("{niche}", niche), user,
                            org_id=org_id, operation="website_change", schema=Meaning, job_id=job_id, max_tokens=400)
    change.summary = data["summary"][:1000]
    change.category = data["category"] if data["category"] in CATEGORIES else category
    change.importance = data["importance"] if data["importance"] in ("high", "medium", "low") else importance
    change.ai = True


async def crawl(session: AsyncSession, website: Website, http: Fetcher, router: AIRouter | None,
                job: Job) -> dict:
    now = utcnow()
    try:
        home = await http.get(website.url)
    except FetchError as e:
        website.status, website.last_error = "error", str(e)
        await session.commit()
        raise CrawlError(str(e)) from e
    if home.status_code != 200:
        website.status, website.last_error = "error", f"Сайт ответил HTTP {home.status_code}"
        await session.commit()
        raise CrawlError(website.last_error)

    first = website.last_crawled_at is None
    limit = await page_limit(session, website.organization_id)
    pages = {p.url: p for p in (await session.execute(
        select(WebsitePage).where(WebsitePage.website_id == website.id))).scalars()}
    tracked = sum(1 for p in pages.values() if p.tracked)
    new_pages: list[WebsitePage] = []
    # найденных страниц храним больше лимита: лишние видны в списке и включаются вручную вместо других
    for url, kind in await discover(http, website.url, home.text, min(max(2 * limit, 30), 500)):
        if url in pages:
            continue
        page = WebsitePage(website_id=website.id, url=url, kind=kind, tracked=tracked < limit)
        tracked += page.tracked
        session.add(page)
        pages[url] = page
        new_pages.append(page)
    await session.flush()
    await set_status(session, job, JobStatus.collecting, progress=20)

    changes: list[tuple[WebsitePage, WebsiteChange]] = []
    todo = [p for p in pages.values() if p.tracked]
    for i, page in enumerate(todo):
        try:
            resp = home if normalize(page.url) == normalize(website.url) else await http.get(page.url)
        except FetchError:
            continue  # сеть моргнула — страница проверится в следующий раз
        page.last_checked_at, prev_status, page.status_code = now, page.status_code, resp.status_code
        if resp.status_code in (404, 410):
            if prev_status == 200:
                changes.append((page, WebsiteChange(website_id=website.id, page_id=page.id, kind="removed_page")))
            page.tracked = False
            continue
        if resp.status_code != 200:
            continue
        prev, snap = await _snapshot(session, page, resp.text)
        if first:
            continue
        if page in new_pages and page.kind in NEW_PAGE_KINDS:
            changes.append((page, WebsiteChange(website_id=website.id, page_id=page.id, kind="new_page",
                                                after_snapshot_id=snap.id)))
        elif prev is not None and snap.id != prev.id:
            added, removed = diff.meaningful_diff(prev.text, snap.text)
            if added or removed:
                changes.append((page, WebsiteChange(website_id=website.id, page_id=page.id, kind="changed",
                                                    added=added, removed=removed, before_snapshot_id=prev.id,
                                                    after_snapshot_id=snap.id)))
        if i % 5 == 4:
            await set_status(session, job, JobStatus.collecting, progress=20 + int(50 * (i + 1) / len(todo)))

    await set_status(session, job, JobStatus.analyzing, progress=75)
    niche = (await taxonomy.get(session, website.organization_id)).niche
    company = website.name or website.url
    if website.competitor_id:
        company = (await session.get(Competitor, website.competitor_id)).name
    note = None
    rank = {"high": 0, "medium": 1, "low": 2}  # модель — сначала на важные изменения
    changes.sort(key=lambda pc: rank[diff.guess(pc[0].kind, pc[1].added or [], pc[1].removed or [])[1]])
    for n, (page, change) in enumerate(changes):
        change.detected_at, change.added, change.removed = now, change.added or [], change.removed or []
        page.last_changed_at = now
        session.add(change)
        try:
            await _explain(router if n < AI_CHANGES_PER_CRAWL else None, niche, website.organization_id, job.id,
                           page, change, company)
        except (AIError, QuotaExceeded) as e:
            router, note = None, f"Смысл части изменений определён по словам: модель недоступна ({e})"
            await _explain(None, niche, website.organization_id, job.id, page, change, company)
    website.status, website.last_error, website.last_crawled_at = "ok", None, now
    await session.commit()
    out = {"pages_checked": len(todo), "pages_total": len(pages), "new_pages": len(new_pages),
           "changes": len(changes), "first_crawl": first}
    if note:
        out["message"] = note
    return out


async def handle_crawl_website(session: AsyncSession, job: Job, *, router: AIRouter | None = None) -> dict:
    website = await session.get(Website, job.params.get("website_id"))
    if website is None or website.organization_id != job.organization_id:
        raise CrawlError("Сайт удалён")
    await set_status(session, job, JobStatus.collecting, progress=5)
    if router is None and settings.openrouter_api_key:
        router = AIRouter(session)
    async with sync.make_fetcher() as http:
        return await crawl(session, website, http, router, job)


async def active_job(session: AsyncSession, org_id: int, website_id: int) -> Job | None:
    return (await session.execute(
        select(Job).where(Job.organization_id == org_id, Job.kind == "crawl_website", Job.status.in_(ACTIVE),
                          Job.params["website_id"].as_integer() == website_id).limit(1))).scalar_one_or_none()


async def start(session: AsyncSession, pool, org_id: int, website_id: int, user_id: int | None = None) -> Job | None:
    if await active_job(session, org_id, website_id):
        return None
    job = await create_job(session, org_id, "crawl_website", {"website_id": website_id}, user_id=user_id)
    await enqueue(pool, job)
    return job


async def schedule_due(session: AsyncSession, pool) -> int:
    due = (await session.execute(select(Website).where(
        (Website.last_crawled_at.is_(None)) |
        (Website.last_crawled_at < utcnow() - timedelta(days=settings.website_crawl_days))))).scalars().all()
    created = 0
    for w in due:
        created += await start(session, pool, w.organization_id, w.id) is not None
    return created


async def change_count(session: AsyncSession, website_id: int, days: int) -> int:
    return (await session.execute(select(func.count()).select_from(WebsiteChange).where(
        WebsiteChange.website_id == website_id, WebsiteChange.detected_at >= utcnow() - timedelta(days=days))
    )).scalar_one()
