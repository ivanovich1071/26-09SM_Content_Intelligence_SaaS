from datetime import datetime, timedelta
from statistics import median

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.openrouter import AIError
from app.analysis import dedupe, pipeline, taxonomy
from app.core.config import settings
from app.core.db import get_session, utcnow
from app.core.deps import Tenant, get_tenant, require_role
from app.models import GlobalPost, GlobalSource, PostAnalysis, Role, Source, SourceRole, Taxonomy

router = APIRouter(prefix="/taxonomy", tags=["taxonomy"])


class TaxonomyOut(BaseModel):
    topics: list[str]
    roles: list[str]
    niche: str | None
    version: int
    source: str | None
    is_default: bool
    universal: dict[str, list[str]]
    updated_at: datetime | None


class TaxonomyIn(BaseModel):
    topics: list[str] = Field(min_length=3, max_length=30)
    roles: list[str] = Field(min_length=2, max_length=15)
    niche: str | None = Field(default=None, max_length=500)
    reclassify: bool = False  # сразу переразметить посты всех источников (тратит AI-бюджет)


async def _out(session: AsyncSession, org_id: int) -> TaxonomyOut:
    tax = (await session.execute(select(Taxonomy).where(Taxonomy.organization_id == org_id))).scalar_one_or_none()
    return TaxonomyOut(
        topics=tax.topics if tax else taxonomy.DEFAULT_TOPICS, roles=tax.roles if tax else taxonomy.DEFAULT_ROLES,
        niche=tax.niche if tax else None, version=tax.version if tax else 0, source=tax.source if tax else None,
        is_default=tax is None, universal=taxonomy.UNIVERSAL, updated_at=tax.updated_at if tax else None)


@router.get("", response_model=TaxonomyOut)
async def get_taxonomy(tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    return await _out(session, tenant.org_id)


@router.put("", response_model=TaxonomyOut)
async def put_taxonomy(body: TaxonomyIn, request: Request, tenant: Tenant = Depends(require_role(Role.admin)),
                       session: AsyncSession = Depends(get_session)):
    if len(taxonomy.clean_labels(body.topics, taxonomy.OTHER_TOPIC)) < 4 \
            or len(taxonomy.clean_labels(body.roles, taxonomy.BROAD_ROLE)) < 3:
        raise HTTPException(422, "Нужно минимум 3 темы и 2 роли (кроме «другое» и «широкая аудитория»)")
    before = await taxonomy.get(session, tenant.org_id)
    tax = await taxonomy.save(session, tenant.org_id, body.topics, body.roles, "manual", body.niche)
    if body.reclassify and tax.version != before.version:
        sources = (await session.execute(tenant.scoped(select(Source.id), Source))).scalars().all()
        for sid in sources:
            await pipeline.start(session, getattr(request.app.state, "arq", None), tenant.org_id, sid, tenant.user.id)
    return await _out(session, tenant.org_id)


@router.post("/suggest")
async def suggest(tenant: Tenant = Depends(require_role(Role.admin)), session: AsyncSession = Depends(get_session)):
    """Модель предлагает темы и роли по источникам и постам организации. Ничего не сохраняет."""
    if not settings.openrouter_api_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "OPENROUTER_API_KEY не задан — подсказка недоступна")
    try:
        return await taxonomy.suggest(session, tenant.org_id)
    except AIError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Модель не ответила: {e}") from e


market_router = APIRouter(prefix="/market", tags=["market"])


@market_router.get("/overview")
async def overview(days: int = 30, tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    """Сводка по источникам организации: объём, вовлечённость, темы и лучшие посты. Числа считает код."""
    days = max(1, min(days, 365))
    since = utcnow() - timedelta(days=days)
    base = (select(GlobalPost, Source, GlobalSource, PostAnalysis)
            .join(GlobalSource, GlobalSource.id == GlobalPost.global_source_id)
            .join(Source, and_(Source.global_source_id == GlobalSource.id, Source.organization_id == tenant.org_id))
            .outerjoin(PostAnalysis, and_(PostAnalysis.post_id == GlobalPost.id,
                                          PostAnalysis.organization_id == tenant.org_id))
            .where(GlobalPost.published_at >= since, dedupe.not_hidden(tenant.org_id),
                   Source.enabled.is_(True)))
    rows = (await session.execute(base)).all()

    by_role: dict[str, dict] = {}
    for role in SourceRole:
        rs = [r for r in rows if r.Source.role == role]
        ers = [r.GlobalPost.er for r in rs if r.GlobalPost.er is not None]
        by_role[role.value] = {"posts": len(rs), "analyzed": sum(1 for r in rs if r.PostAnalysis and not
                                                                   r.PostAnalysis.error),
                               "median_er": round(median(ers), 2) if ers else None,
                               "posts_per_week": round(len(rs) / (days / 7), 1)}
    topics: dict[str, dict[str, int]] = {}
    for r in rows:
        if r.PostAnalysis and not r.PostAnalysis.error and r.PostAnalysis.topic:
            cell = topics.setdefault(r.PostAnalysis.topic, {"own": 0, "competitor": 0, "market": 0})
            cell[r.Source.role.value] += 1
    top = sorted((r for r in rows if r.GlobalPost.overperformance and r.Source.role != SourceRole.own),
                 key=lambda r: r.GlobalPost.overperformance, reverse=True)[:5]
    return {
        "days": days, "by_role": by_role,
        "topics": sorted(({"topic": t, **c, "total": sum(c.values())} for t, c in topics.items()),
                         key=lambda x: x["total"], reverse=True),
        "top_posts": [{"id": r.GlobalPost.id, "source": r.Source.name or r.GlobalSource.title or r.GlobalSource.key,
                       "role": r.Source.role.value, "url": r.GlobalPost.url,
                       "text": (r.GlobalPost.title or r.GlobalPost.text or "")[:280],
                       "published_at": r.GlobalPost.published_at, "views": r.GlobalPost.views,
                       "er": r.GlobalPost.er, "overperformance": r.GlobalPost.overperformance,
                       "topic": r.PostAnalysis.topic if r.PostAnalysis else None,
                       "summary": r.PostAnalysis.summary if r.PostAnalysis else None} for r in top],
    }
