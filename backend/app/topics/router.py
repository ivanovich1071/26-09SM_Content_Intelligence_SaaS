from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.openrouter import AIError
from app.core.config import settings
from app.core.db import get_session
from app.core.deps import Tenant, get_tenant, require_role
from app.jobs import service as jobs
from app.models import FINAL_STATUSES, GlobalPost, Job, Role, TopicCluster
from app.posts.router import FeedPost, feed_post, feed_query
from app.sources.router import JobBrief
from app.topics import service, stats

router = APIRouter(prefix="/topics", tags=["topics"])
Days = Query(default=30, ge=7, le=365)


class ExplainIn(BaseModel):
    topic: str = Field(min_length=1, max_length=120)
    days: int = Field(default=90, ge=7, le=365)
    force: bool = False


async def _cluster_job(session: AsyncSession, org_id: int) -> Job | None:
    return (await session.execute(select(Job).where(Job.organization_id == org_id, Job.kind == "cluster_topics")
                                  .order_by(Job.id.desc()).limit(1))).scalar_one_or_none()


def _insight_out(i) -> dict | None:
    return {"data": i.data, "days": i.days, "created_at": i.created_at} if i else None


@router.get("")
async def list_topics(days: int = Days, tenant: Tenant = Depends(get_tenant),
                      session: AsyncSession = Depends(get_session)):
    """Темы таксономии с долями, gap, трендом и насыщенностью + число под-тем."""
    data = await stats.overview(session, tenant.org_id, days)
    subs = {}
    for c in await _all_clusters(session, tenant.org_id):
        subs.setdefault(c.topic, []).append({"id": c.id, "label": c.label, "size": c.size})
    for t in data["topics"]:
        t["subtopics"] = subs.get(t["topic"], [])
    job = await _cluster_job(session, tenant.org_id)
    data["cluster_job"] = JobBrief.model_validate(job) if job else None
    return data


async def _all_clusters(session: AsyncSession, org_id: int):
    return (await session.execute(select(TopicCluster).where(TopicCluster.organization_id == org_id)
                                  .order_by(TopicCluster.size.desc()))).scalars().all()


@router.get("/gaps")
async def gaps(days: int = Query(default=90, ge=7, le=365), tenant: Tenant = Depends(get_tenant),
               session: AsyncSession = Depends(get_session)):
    """Content Gaps: темы, где доля рынка выше вашей минимум на stats.GAP_MIN_PP п.п."""
    data = await stats.overview(session, tenant.org_id, days)
    cached = await service.insights(session, tenant.org_id)
    items = sorted((t for t in data["topics"] if t["is_gap"]), key=lambda t: t["gap"], reverse=True)
    return {"days": days, "own_total": data["own_total"], "market_total": data["market_total"],
            "threshold_pp": stats.GAP_MIN_PP,
            "gaps": [{**t, "insight": _insight_out(cached.get(t["topic"]))} for t in items]}


@router.get("/detail")
async def topic_detail(topic: str = Query(min_length=1, max_length=120), days: int = Days,
                       tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    d = await service.detail(session, tenant.org_id, topic, days)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "За этот период публикаций по теме нет")
    ids = d.pop("top_post_ids") + d.pop("own_post_ids")
    rows = (await session.execute(feed_query(tenant.org_id).where(GlobalPost.id.in_(ids)))).all() if ids else []
    posts: dict[int, FeedPost] = {r[0].id: feed_post(r) for r in rows}
    d["top_posts"] = [posts[i] for i in ids[:service.TOP_POSTS] if i in posts and posts[i].source.role != "own"]
    d["own_posts"] = [p for p in posts.values() if p.source.role == "own"]
    d["insight"] = _insight_out((await service.insights(session, tenant.org_id)).get(topic))
    return d


@router.post("/gaps/explain")
async def explain_gap(body: ExplainIn, tenant: Tenant = Depends(require_role(Role.member)),
                      session: AsyncSession = Depends(get_session)):
    """AI-объяснение gap. Кэшируется на тему; force=true — заново."""
    cached = (await service.insights(session, tenant.org_id)).get(body.topic)
    if cached and not body.force:
        return _insight_out(cached)
    if not settings.openrouter_api_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "OPENROUTER_API_KEY не задан — объяснение недоступно")
    try:
        await service.explain(session, tenant.org_id, body.topic, body.days)
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "За этот период публикаций по теме нет") from e
    except AIError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Модель не ответила: {e}") from e
    return _insight_out((await service.insights(session, tenant.org_id))[body.topic])


@router.post("/cluster", response_model=JobBrief, status_code=status.HTTP_202_ACCEPTED)
async def cluster(request: Request, tenant: Tenant = Depends(require_role(Role.member)),
                  session: AsyncSession = Depends(get_session)):
    """Пересчитать под-темы (HDBSCAN по эмбеддингам + названия от модели)."""
    last = await _cluster_job(session, tenant.org_id)
    if last and last.status not in FINAL_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Под-темы уже пересчитываются")
    job = await jobs.create_job(session, tenant.org_id, "cluster_topics", user_id=tenant.user.id)
    await jobs.enqueue(getattr(request.app.state, "arq", None), job)
    return job
