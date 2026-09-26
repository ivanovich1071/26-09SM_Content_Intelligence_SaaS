from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import dedupe
from app.billing import quotas
from app.competitors import profile, stats
from app.connectors import InvalidSource, social_kind
from app.connectors.base import normalize_web_url
from app.connectors.http import Fetcher, FetchError
from app.connectors.website import WebsiteConnector
from app.core.config import settings
from app.core.db import get_session
from app.core.deps import Tenant, get_tenant, require_role
from app.models import Competitor, GlobalPost, Job, PostAnalysis, PostEmbedding, Role, Source, SourceKind, SourceRole
from app.sources import service
from app.sources.router import JobBrief, PostOut, SourceOut, post_out, sources_out

router = APIRouter(prefix="/competitors", tags=["competitors"])

KEYS_NEEDED = {"instagram": "apify_token", "vk": "vk_service_token"}


class CompetitorIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=1000)
    notes: str | None = Field(default=None, max_length=5000)
    sources: list[str] = Field(default_factory=list, max_length=20)  # адреса каналов; сайт добавляется отдельно
    track_website: bool = True  # добавить сайт как источник (статьи блога через RSS)


class CompetitorPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=1000)
    notes: str | None = Field(default=None, max_length=5000)


class SourceAdd(BaseModel):
    url: str = Field(min_length=2, max_length=1000)
    kind: SourceKind | None = None


class DiscoverIn(BaseModel):
    website: str = Field(min_length=3, max_length=1000)


class CompetitorOut(BaseModel):
    id: int
    name: str
    website: str | None
    notes: str | None
    created_at: datetime
    sources: list[SourceOut]
    stats: dict
    profile: dict | None
    profile_at: datetime | None
    profile_job: JobBrief | None


async def _sources(session: AsyncSession, competitor: Competitor) -> list[Source]:
    return list((await session.execute(
        select(Source).where(Source.competitor_id == competitor.id).order_by(Source.id))).scalars())


async def _out(session: AsyncSession, competitor: Competitor, *, days: int = 30) -> CompetitorOut:
    srcs = await _sources(session, competitor)
    job = (await session.execute(
        select(Job).where(Job.organization_id == competitor.organization_id, Job.kind == "profile_competitor",
                          Job.params["competitor_id"].as_integer() == competitor.id)
        .order_by(Job.id.desc()).limit(1))).scalar_one_or_none()
    return CompetitorOut(
        id=competitor.id, name=competitor.name, website=competitor.website, notes=competitor.notes,
        created_at=competitor.created_at, sources=await sources_out(session, competitor.organization_id, srcs),
        stats=await stats.compute(session, competitor.organization_id, [s.id for s in srcs if s.enabled], days),
        profile=competitor.profile, profile_at=competitor.profile_at,
        profile_job=JobBrief.model_validate(job) if job else None)


async def _link_sources(request: Request, session: AsyncSession, tenant: Tenant, competitor: Competitor,
                        specs: list[tuple[SourceKind, str, str]]) -> None:
    """Создаёт источники конкурента одним решением по квоте. Уже подключённый у организации канал
    привязывается к конкуренту, а не дублируется; канал другого конкурента — 409."""
    plan: list[tuple] = []
    for kind, key, url in dict.fromkeys(specs):
        gs = await service.global_source(session, kind, key, url)
        src = await service.existing(session, tenant.org_id, gs.id)
        if src and src.competitor_id not in (None, competitor.id):
            raise HTTPException(status.HTTP_409_CONFLICT, f"{url} уже привязан к другому конкуренту")
        plan.append((gs, src))
    new = sum(1 for _, src in plan if src is None)
    if new:
        await quotas.check(session, tenant.org_id, "sources", amount=new,
                           current=await service.count(session, tenant.org_id))
    created = []
    for gs, src in plan:
        if src is None:
            src = Source(organization_id=tenant.org_id, global_source_id=gs.id, created_by=tenant.user.id)
            session.add(src)
            created.append(src)
        src.role, src.competitor_id = SourceRole.competitor, competitor.id
    await session.commit()
    for src in created:
        await service.start_sync(session, getattr(request.app.state, "arq", None), tenant.org_id, src, tenant.user.id)


def _resolve_all(urls: list[str], website: str | None, track_website: bool) -> list[tuple[SourceKind, str, str]]:
    specs = []
    try:
        for url in urls:
            if url.strip():
                specs.append(service.resolve(url))
        if website and track_website:
            specs.append(service.resolve(website, SourceKind.website))
    except InvalidSource as e:
        raise HTTPException(422, str(e)) from e
    return specs


async def _competitor_count(session: AsyncSession, org_id: int) -> int:
    return (await session.execute(
        select(func.count()).select_from(Competitor).where(Competitor.organization_id == org_id))).scalar_one()


@router.get("", response_model=list[CompetitorOut])
async def list_competitors(tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(tenant.scoped(select(Competitor), Competitor).order_by(Competitor.name))).scalars()
    return [await _out(session, c) for c in rows]


@router.post("/discover")
async def discover(body: DiscoverIn, tenant: Tenant = Depends(require_role(Role.member))):
    """Открывает сайт и находит соцсети и RSS — пользователь выбирает, что подключить. Ничего не сохраняет."""
    site = WebsiteConnector()
    try:
        key, url = site.normalize(body.website)
    except InvalidSource as e:
        raise HTTPException(422, str(e)) from e
    try:
        async with Fetcher() as http:
            prof = await site.get_profile(http, key, url, {})
    except FetchError as e:
        raise HTTPException(422, f"Сайт не открылся: {e}") from e
    if not prof.available:
        raise HTTPException(422, prof.reason or "Сайт недоступен")
    links = []
    for link in prof.meta.get("social_links", []):
        kind = social_kind(link)
        need = KEYS_NEEDED.get(kind or "")
        links.append({"url": link, "kind": kind, "supported": kind is not None,
                      "needs_key": bool(need and not getattr(settings, need))})
    return {"website": url, "title": prof.title, "description": prof.description,
            "feed_url": prof.meta.get("feed_url"), "social_links": links}


@router.post("", response_model=CompetitorOut, status_code=status.HTTP_201_CREATED)
async def create_competitor(body: CompetitorIn, request: Request, tenant: Tenant = Depends(require_role(Role.member)),
                            session: AsyncSession = Depends(get_session)):
    await quotas.check(session, tenant.org_id, "competitors", current=await _competitor_count(session, tenant.org_id))
    specs = _resolve_all(body.sources, body.website, body.track_website)
    try:
        website = normalize_web_url(body.website) if body.website else None
    except InvalidSource as e:
        raise HTTPException(422, str(e)) from e
    competitor = Competitor(organization_id=tenant.org_id, name=body.name.strip(), website=website,
                            notes=body.notes, created_by=tenant.user.id)
    session.add(competitor)
    try:
        await session.flush()
    except IntegrityError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, "Конкурент с таким названием уже есть") from e
    try:
        await _link_sources(request, session, tenant, competitor, specs)
    except HTTPException:
        await session.rollback()
        raise
    return await _out(session, competitor)


@router.get("/{competitor_id}", response_model=CompetitorOut)
async def get_competitor(competitor_id: int, days: int = 30, tenant: Tenant = Depends(get_tenant),
                         session: AsyncSession = Depends(get_session)):
    return await _out(session, await tenant.get(session, Competitor, competitor_id), days=max(1, min(days, 365)))


@router.patch("/{competitor_id}", response_model=CompetitorOut)
async def update_competitor(competitor_id: int, body: CompetitorPatch,
                            tenant: Tenant = Depends(require_role(Role.member)),
                            session: AsyncSession = Depends(get_session)):
    competitor: Competitor = await tenant.get(session, Competitor, competitor_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(competitor, field, value.strip() if field == "name" and value else value)
    try:
        await session.commit()
    except IntegrityError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, "Конкурент с таким названием уже есть") from e
    return await _out(session, competitor)


@router.delete("/{competitor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_competitor(competitor_id: int, tenant: Tenant = Depends(require_role(Role.member)),
                            session: AsyncSession = Depends(get_session)):
    """Удаляет конкурента и его подключения. Собранные посты остаются в общем хранилище."""
    competitor = await tenant.get(session, Competitor, competitor_id)
    await session.delete(competitor)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{competitor_id}/sources", response_model=CompetitorOut, status_code=status.HTTP_201_CREATED)
async def add_competitor_source(competitor_id: int, body: SourceAdd, request: Request,
                                tenant: Tenant = Depends(require_role(Role.member)),
                                session: AsyncSession = Depends(get_session)):
    competitor: Competitor = await tenant.get(session, Competitor, competitor_id)
    try:
        spec = service.resolve(body.url, body.kind)
    except InvalidSource as e:
        raise HTTPException(422, str(e)) from e
    await _link_sources(request, session, tenant, competitor, [spec])
    return await _out(session, competitor)


@router.post("/{competitor_id}/profile", response_model=CompetitorOut, status_code=status.HTTP_202_ACCEPTED)
async def refresh_profile(competitor_id: int, request: Request, tenant: Tenant = Depends(require_role(Role.member)),
                          session: AsyncSession = Depends(get_session)):
    competitor: Competitor = await tenant.get(session, Competitor, competitor_id)
    if not settings.openrouter_api_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "OPENROUTER_API_KEY не задан — профиль недоступен")
    if await profile.start(session, getattr(request.app.state, "arq", None), tenant.org_id, competitor.id,
                           tenant.user.id) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Профиль уже строится")
    return await _out(session, competitor)


@router.get("/{competitor_id}/posts", response_model=list[PostOut])
async def competitor_posts(competitor_id: int, limit: int = 30, sort: str = "recent",
                           tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    """Контент конкурента: свежие (sort=recent) или лучшие по overperformance (sort=top)."""
    competitor: Competitor = await tenant.get(session, Competitor, competitor_id)
    order = (GlobalPost.overperformance.desc().nulls_last() if sort == "top"
             else GlobalPost.published_at.desc().nulls_last())
    stmt = (select(GlobalPost, PostAnalysis, PostEmbedding.post_id.is_not(None))
            .join(Source, and_(Source.global_source_id == GlobalPost.global_source_id,
                               Source.organization_id == tenant.org_id))
            .outerjoin(PostAnalysis, and_(PostAnalysis.post_id == GlobalPost.id,
                                          PostAnalysis.organization_id == tenant.org_id))
            .outerjoin(PostEmbedding, PostEmbedding.post_id == GlobalPost.id)
            .where(Source.competitor_id == competitor.id, dedupe.not_hidden(tenant.org_id))
            .order_by(order, GlobalPost.id.desc()).limit(min(limit, 100)))
    return [post_out(p, a, bool(e)) for p, a, e in (await session.execute(stmt)).all()]
