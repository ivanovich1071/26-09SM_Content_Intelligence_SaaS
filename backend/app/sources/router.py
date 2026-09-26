from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import pipeline
from app.billing import quotas
from app.connectors import InvalidSource, detect_kind, get_connector
from app.core.db import get_session
from app.core.deps import Tenant, get_tenant, require_role
from app.jobs import service as jobs
from app.models import (
    GlobalPost,
    GlobalSource,
    Job,
    PostAnalysis,
    PostEmbedding,
    Role,
    Source,
    SourceKind,
    SourceRole,
    SourceStatus,
)
from app.sources.sync import active_job

router = APIRouter(prefix="/sources", tags=["sources"])


class SourceIn(BaseModel):
    url: str = Field(min_length=2, max_length=1000, description="t.me/канал, @канал, адрес сайта или RSS")
    kind: SourceKind | None = None  # по умолчанию определяется по адресу
    role: SourceRole = SourceRole.market
    name: str | None = Field(default=None, max_length=200)


class SourcePatch(BaseModel):
    role: SourceRole | None = None
    name: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None


class JobBrief(BaseModel):
    id: int
    status: str
    progress: int
    result: dict | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class SourceOut(BaseModel):
    id: int
    kind: SourceKind
    key: str
    url: str
    role: SourceRole
    name: str | None
    enabled: bool
    title: str | None
    description: str | None
    followers: int | None
    status: SourceStatus
    last_error: str | None
    last_synced_at: datetime | None
    posts_count: int
    meta: dict
    last_job: JobBrief | None
    last_analysis: JobBrief | None
    median_views: float | None
    analyzed_count: int  # размечено этой организацией (без ошибок)
    created_at: datetime


class PostOut(BaseModel):
    id: int
    external_id: str
    url: str | None
    title: str | None
    text: str
    published_at: datetime | None
    media_type: str
    views: int | None
    likes: int | None
    comments: int | None
    shares: int | None
    engagement: int | None
    er: float | None
    overperformance: float | None
    duplicate_of_id: int | None
    has_embedding: bool = False
    analysis: dict | None = None


ANALYSIS_FIELDS = ("content_type", "funnel_stage", "hook_type", "cta_type", "proof_type", "tone", "value_type",
                   "topic", "target_role", "has_case", "has_numbers", "has_offer", "has_lead_magnet", "summary",
                   "error", "taxonomy_version", "analyzed_at")


def post_out(p: GlobalPost, a: PostAnalysis | None, has_embedding: bool) -> PostOut:
    return PostOut(
        id=p.id, external_id=p.external_id, url=p.url, title=p.title, text=p.text, published_at=p.published_at,
        media_type=p.media_type, views=p.views, likes=p.likes, comments=p.comments, shares=p.shares,
        engagement=p.engagement, er=p.er, overperformance=p.overperformance, duplicate_of_id=p.duplicate_of_id,
        has_embedding=has_embedding, analysis={f: getattr(a, f) for f in ANALYSIS_FIELDS} if a else None)


async def _out(session: AsyncSession, org_id: int, sources: list[Source]) -> list[SourceOut]:
    if not sources:
        return []
    gs_ids = [s.global_source_id for s in sources]
    counts = dict((await session.execute(
        select(GlobalPost.global_source_id, func.count()).where(GlobalPost.global_source_id.in_(gs_ids))
        .group_by(GlobalPost.global_source_id))).all())
    analyzed = dict((await session.execute(
        select(GlobalPost.global_source_id, func.count()).join(PostAnalysis, PostAnalysis.post_id == GlobalPost.id)
        .where(GlobalPost.global_source_id.in_(gs_ids), PostAnalysis.organization_id == org_id,
               PostAnalysis.error.is_(None))
        .group_by(GlobalPost.global_source_id))).all())
    src_ids = {s.id for s in sources}
    last_jobs: dict[tuple[str, int], Job] = {}
    for job in (await session.execute(
            select(Job).where(Job.organization_id == org_id, Job.kind.in_(("sync_source", "analyze_source")))
            .order_by(Job.id.desc()).limit(1000))).scalars():
        sid = (job.params or {}).get("source_id")
        if sid in src_ids and (job.kind, sid) not in last_jobs:
            last_jobs[(job.kind, sid)] = job
    out = []
    for s in sources:
        gs = s.global_source
        job = last_jobs.get(("sync_source", s.id))
        analysis_job = last_jobs.get(("analyze_source", s.id))
        out.append(SourceOut(
            id=s.id, kind=gs.kind, key=gs.key, url=gs.url, role=s.role, name=s.name, enabled=s.enabled,
            title=gs.title, description=gs.description, followers=gs.followers, status=gs.status,
            last_error=gs.last_error, last_synced_at=gs.last_synced_at, posts_count=counts.get(gs.id, 0),
            meta=gs.meta or {}, last_job=JobBrief.model_validate(job) if job else None,
            last_analysis=JobBrief.model_validate(analysis_job) if analysis_job else None,
            median_views=gs.median_views, analyzed_count=analyzed.get(gs.id, 0), created_at=s.created_at))
    return out


async def _start_sync(request: Request, session: AsyncSession, tenant: Tenant, source: Source) -> None:
    job = await jobs.create_job(session, tenant.org_id, "sync_source", {"source_id": source.id},
                                user_id=tenant.user.id)
    await jobs.enqueue(getattr(request.app.state, "arq", None), job)


@router.get("", response_model=list[SourceOut])
async def list_sources(tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(tenant.scoped(select(Source), Source).order_by(Source.id))).scalars()
    return await _out(session, tenant.org_id, list(rows))


@router.post("", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
async def add_source(body: SourceIn, request: Request, tenant: Tenant = Depends(require_role(Role.member)),
                     session: AsyncSession = Depends(get_session)):
    kind = body.kind or SourceKind(detect_kind(body.url))
    try:
        key, url = get_connector(kind).normalize(body.url)
    except InvalidSource as e:
        raise HTTPException(422, str(e)) from e

    count = (await session.execute(
        tenant.scoped(select(func.count()).select_from(Source), Source))).scalar_one()
    await quotas.check(session, tenant.org_id, "sources", current=count)

    await session.execute(insert(GlobalSource).values(kind=kind, key=key, url=url, status=SourceStatus.new, meta={})
                          .on_conflict_do_nothing(constraint="uq_global_sources_kind_key"))
    gs = (await session.execute(
        select(GlobalSource).where(GlobalSource.kind == kind, GlobalSource.key == key))).scalar_one()
    exists = (await session.execute(tenant.scoped(
        select(Source.id).where(Source.global_source_id == gs.id), Source))).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Этот источник уже добавлен")

    source = Source(organization_id=tenant.org_id, global_source_id=gs.id, role=body.role, name=body.name,
                    created_by=tenant.user.id)
    session.add(source)
    await session.commit()
    await session.refresh(source, ["global_source"])
    await _start_sync(request, session, tenant, source)
    return (await _out(session, tenant.org_id, [source]))[0]


@router.get("/{source_id}", response_model=SourceOut)
@router.get("/{source_id}/status", response_model=SourceOut)
async def source_status(source_id: int, tenant: Tenant = Depends(get_tenant),
                        session: AsyncSession = Depends(get_session)):
    source = await tenant.get(session, Source, source_id)
    return (await _out(session, tenant.org_id, [source]))[0]


@router.patch("/{source_id}", response_model=SourceOut)
async def update_source(source_id: int, body: SourcePatch, tenant: Tenant = Depends(require_role(Role.member)),
                        session: AsyncSession = Depends(get_session)):
    source: Source = await tenant.get(session, Source, source_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None or field == "name":
            setattr(source, field, value)
    await session.commit()
    return (await _out(session, tenant.org_id, [source]))[0]


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(source_id: int, tenant: Tenant = Depends(require_role(Role.member)),
                        session: AsyncSession = Depends(get_session)):
    """Удаляет подключение организации. Собранные посты остаются в общем хранилище."""
    source = await tenant.get(session, Source, source_id)
    await session.delete(source)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{source_id}/sync", response_model=SourceOut, status_code=status.HTTP_202_ACCEPTED)
async def sync_source(source_id: int, request: Request, tenant: Tenant = Depends(require_role(Role.member)),
                      session: AsyncSession = Depends(get_session)):
    source: Source = await tenant.get(session, Source, source_id)
    if await active_job(session, tenant.org_id, source.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Синхронизация уже идёт")
    await _start_sync(request, session, tenant, source)
    return (await _out(session, tenant.org_id, [source]))[0]


@router.get("/{source_id}/posts", response_model=list[PostOut])
async def source_posts(source_id: int, limit: int = 20, tenant: Tenant = Depends(get_tenant),
                       session: AsyncSession = Depends(get_session)):
    """Последние посты источника — для проверки сбора. Полноценная лента с фильтрами — EPIC 5."""
    source: Source = await tenant.get(session, Source, source_id)
    stmt = (select(GlobalPost, PostAnalysis, PostEmbedding.post_id.is_not(None))
            .outerjoin(PostAnalysis, and_(PostAnalysis.post_id == GlobalPost.id,
                                          PostAnalysis.organization_id == tenant.org_id))
            .outerjoin(PostEmbedding, PostEmbedding.post_id == GlobalPost.id)
            .where(GlobalPost.global_source_id == source.global_source_id)
            .order_by(GlobalPost.published_at.desc().nulls_last(), GlobalPost.id.desc()).limit(min(limit, 100)))
    return [post_out(p, a, bool(e)) for p, a, e in (await session.execute(stmt)).all()]


@router.post("/{source_id}/analyze", response_model=SourceOut, status_code=status.HTTP_202_ACCEPTED)
async def analyze_source(source_id: int, request: Request, tenant: Tenant = Depends(require_role(Role.member)),
                         session: AsyncSession = Depends(get_session)):
    """Метрики, дубли, разметка и эмбеддинги без нового сбора — например, после правки таксономии."""
    source: Source = await tenant.get(session, Source, source_id)
    if await pipeline.start(session, getattr(request.app.state, "arq", None), tenant.org_id, source.id,
                            tenant.user.id) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Анализ уже идёт")
    return (await _out(session, tenant.org_id, [source]))[0]
