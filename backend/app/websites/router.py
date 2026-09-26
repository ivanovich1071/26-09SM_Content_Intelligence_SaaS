from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import quotas
from app.connectors.base import InvalidSource, normalize_web_url
from app.core.db import get_session, utcnow
from app.core.deps import Tenant, get_tenant, require_role
from app.models import Competitor, Job, PageSnapshot, Role, Website, WebsiteChange, WebsitePage
from app.sources.router import JobBrief
from app.websites import crawl

router = APIRouter(prefix="/websites", tags=["websites"])


class WebsiteIn(BaseModel):
    url: str = Field(min_length=3, max_length=1000)
    name: str | None = Field(default=None, max_length=200)
    competitor_id: int | None = None


class PagePatch(BaseModel):
    tracked: bool


class PageOut(BaseModel):
    id: int
    url: str
    kind: str
    title: str | None
    tracked: bool
    status_code: int | None
    last_checked_at: datetime | None
    last_changed_at: datetime | None

    model_config = {"from_attributes": True}


class WebsiteOut(BaseModel):
    id: int
    url: str
    name: str | None
    competitor_id: int | None
    competitor_name: str | None
    status: str
    last_error: str | None
    last_crawled_at: datetime | None
    pages_tracked: int
    pages_total: int
    page_limit: int
    changes_30d: int
    last_job: JobBrief | None
    created_at: datetime


class ChangeOut(BaseModel):
    id: int
    website_id: int
    website: str
    competitor_id: int | None
    competitor_name: str | None
    page_id: int
    page_url: str
    page_kind: str
    page_title: str | None
    kind: str
    detected_at: datetime
    summary: str | None
    category: str | None
    importance: str | None
    ai: bool
    added_count: int
    removed_count: int


class ChangeDetail(ChangeOut):
    added: list[str]
    removed: list[str]
    before_text: str | None
    after_text: str | None


async def _out(session: AsyncSession, w: Website) -> WebsiteOut:
    counts = (await session.execute(select(func.count(), func.count().filter(WebsitePage.tracked))
                                    .where(WebsitePage.website_id == w.id))).one()
    job = (await session.execute(select(Job).where(
        Job.organization_id == w.organization_id, Job.kind == "crawl_website",
        Job.params["website_id"].as_integer() == w.id).order_by(Job.id.desc()).limit(1))).scalar_one_or_none()
    comp = await session.get(Competitor, w.competitor_id) if w.competitor_id else None
    return WebsiteOut(
        id=w.id, url=w.url, name=w.name, competitor_id=w.competitor_id, competitor_name=comp.name if comp else None,
        status=w.status, last_error=w.last_error, last_crawled_at=w.last_crawled_at, pages_total=counts[0],
        pages_tracked=counts[1], page_limit=await crawl.page_limit(session, w.organization_id),
        changes_30d=await crawl.change_count(session, w.id, 30), last_job=JobBrief.model_validate(job) if job else None,
        created_at=w.created_at)


def _change_rows(org_id: int):
    return (select(WebsiteChange, Website, WebsitePage, Competitor.name)
            .join(Website, Website.id == WebsiteChange.website_id)
            .join(WebsitePage, WebsitePage.id == WebsiteChange.page_id)
            .outerjoin(Competitor, Competitor.id == Website.competitor_id)
            .where(Website.organization_id == org_id))


def _change_out(row) -> dict:
    c, w, p, comp = row
    return dict(id=c.id, website_id=w.id, website=w.name or w.url, competitor_id=w.competitor_id,
                competitor_name=comp, page_id=p.id, page_url=p.url, page_kind=p.kind, page_title=p.title,
                kind=c.kind, detected_at=c.detected_at, summary=c.summary, category=c.category,
                importance=c.importance, ai=c.ai, added_count=len(c.added or []), removed_count=len(c.removed or []))


@router.get("", response_model=list[WebsiteOut])
async def list_websites(tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(tenant.scoped(select(Website), Website).order_by(Website.id))).scalars()
    return [await _out(session, w) for w in rows]


@router.post("", response_model=WebsiteOut, status_code=status.HTTP_201_CREATED)
async def add_website(body: WebsiteIn, request: Request, tenant: Tenant = Depends(require_role(Role.member)),
                      session: AsyncSession = Depends(get_session)):
    try:
        url = normalize_web_url(body.url)
    except InvalidSource as e:
        raise HTTPException(422, str(e)) from e
    if body.competitor_id is not None:
        await tenant.get(session, Competitor, body.competitor_id)
    count = (await session.execute(tenant.scoped(select(func.count()).select_from(Website), Website))).scalar_one()
    await quotas.check(session, tenant.org_id, "websites", current=count)
    website = Website(organization_id=tenant.org_id, url=url, name=body.name, competitor_id=body.competitor_id,
                      created_by=tenant.user.id)
    session.add(website)
    try:
        await session.commit()
    except IntegrityError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, "Этот сайт уже отслеживается") from e
    await crawl.start(session, getattr(request.app.state, "arq", None), tenant.org_id, website.id, tenant.user.id)
    return await _out(session, website)


@router.get("/changes", response_model=list[ChangeOut])
async def changes(website_id: int | None = None, competitor_id: int | None = None,
                  importance: list[str] = Query(default=[]), days: int = Query(default=90, ge=1, le=365),
                  limit: int = Query(default=50, ge=1, le=200), tenant: Tenant = Depends(get_tenant),
                  session: AsyncSession = Depends(get_session)):
    """Лента изменений сайтов: сначала свежие; фильтры — сайт, конкурент, важность."""
    stmt = _change_rows(tenant.org_id).where(WebsiteChange.detected_at >= utcnow() - timedelta(days=days))
    if website_id is not None:
        stmt = stmt.where(Website.id == website_id)
    if competitor_id is not None:
        stmt = stmt.where(Website.competitor_id == competitor_id)
    if importance:
        stmt = stmt.where(WebsiteChange.importance.in_(importance))
    rank = case({"high": 0, "medium": 1}, value=WebsiteChange.importance, else_=2)  # в одном обходе — важные выше
    rows = (await session.execute(stmt.order_by(WebsiteChange.detected_at.desc(), rank, WebsiteChange.id.desc())
                                  .limit(limit))).all()
    return [ChangeOut(**_change_out(r)) for r in rows]


@router.get("/changes/{change_id}", response_model=ChangeDetail)
async def change_detail(change_id: int, tenant: Tenant = Depends(get_tenant),
                        session: AsyncSession = Depends(get_session)):
    row = (await session.execute(_change_rows(tenant.org_id).where(WebsiteChange.id == change_id))).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Не найдено")
    c = row[0]
    before = await session.get(PageSnapshot, c.before_snapshot_id) if c.before_snapshot_id else None
    after = await session.get(PageSnapshot, c.after_snapshot_id) if c.after_snapshot_id else None
    return ChangeDetail(**_change_out(row), added=c.added or [], removed=c.removed or [],
                        before_text=before.text if before else None, after_text=after.text if after else None)


@router.get("/{website_id}", response_model=WebsiteOut)
async def get_website(website_id: int, tenant: Tenant = Depends(get_tenant),
                      session: AsyncSession = Depends(get_session)):
    return await _out(session, await tenant.get(session, Website, website_id))


@router.get("/{website_id}/pages", response_model=list[PageOut])
async def pages(website_id: int, tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    w = await tenant.get(session, Website, website_id)
    rows = (await session.execute(select(WebsitePage).where(WebsitePage.website_id == w.id)
                                  .order_by(WebsitePage.tracked.desc(), WebsitePage.kind, WebsitePage.url))).scalars()
    return list(rows)


@router.patch("/{website_id}/pages/{page_id}", response_model=PageOut)
async def toggle_page(website_id: int, page_id: int, body: PagePatch,
                      tenant: Tenant = Depends(require_role(Role.member)),
                      session: AsyncSession = Depends(get_session)):
    w = await tenant.get(session, Website, website_id)
    page = await session.get(WebsitePage, page_id)
    if page is None or page.website_id != w.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Не найдено")
    if body.tracked and not page.tracked:
        tracked = (await session.execute(select(func.count()).select_from(WebsitePage).where(
            WebsitePage.website_id == w.id, WebsitePage.tracked))).scalar_one()
        await quotas.check(session, tenant.org_id, "website_pages", current=tracked)
    page.tracked = body.tracked
    await session.commit()
    return page


@router.post("/{website_id}/crawl", response_model=WebsiteOut, status_code=status.HTTP_202_ACCEPTED)
async def crawl_now(website_id: int, request: Request, tenant: Tenant = Depends(require_role(Role.member)),
                    session: AsyncSession = Depends(get_session)):
    w = await tenant.get(session, Website, website_id)
    if await crawl.start(session, getattr(request.app.state, "arq", None), tenant.org_id, w.id, tenant.user.id) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Обход уже идёт")
    return await _out(session, w)


@router.delete("/{website_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_website(website_id: int, tenant: Tenant = Depends(require_role(Role.member)),
                         session: AsyncSession = Depends(get_session)):
    await session.delete(await tenant.get(session, Website, website_id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
