"""Лента: публикации всех источников организации с фильтрами, поиском и курсорной пагинацией."""
import base64
import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingError, get_embedding_provider
from app.ai.openrouter import AIError
from app.analysis import dedupe
from app.analysis.embed import embed_query
from app.core.config import settings
from app.core.db import get_session
from app.core.deps import Tenant, get_tenant, require_role
from app.models import (
    Competitor,
    GlobalPost,
    GlobalSource,
    PostAnalysis,
    PostEmbedding,
    Role,
    Source,
    SourceKind,
    SourceRole,
)
from app.posts import insight as insights
from app.sources.router import PostOut, post_out

router = APIRouter(prefix="/posts", tags=["posts"])

Sort = Literal["recent", "top", "er", "relevance"]


class SourceBrief(BaseModel):
    id: int
    name: str
    kind: SourceKind
    role: SourceRole
    competitor_id: int | None
    competitor_name: str | None


class FeedPost(PostOut):
    source: SourceBrief


class FeedPage(BaseModel):
    items: list[FeedPost]
    next_cursor: str | None
    search_mode: str | None = None  # text | semantic — чем реально искали
    notice: str | None = None


class PostDetail(BaseModel):
    post: FeedPost
    source_median_views: float | None
    source_median_engagement: float | None
    insight: dict | None
    insight_at: datetime | None
    similar: list[FeedPost]


def feed_query(org_id: int) -> Select:
    return (select(GlobalPost, PostAnalysis, Source, GlobalSource, Competitor.name,
                   PostEmbedding.post_id.is_not(None).label("has_emb"))
            .join(GlobalSource, GlobalSource.id == GlobalPost.global_source_id)
            .join(Source, and_(Source.global_source_id == GlobalSource.id, Source.organization_id == org_id))
            .outerjoin(PostAnalysis, and_(PostAnalysis.post_id == GlobalPost.id,
                                          PostAnalysis.organization_id == org_id))
            .outerjoin(PostEmbedding, PostEmbedding.post_id == GlobalPost.id)
            .outerjoin(Competitor, Competitor.id == Source.competitor_id))


def feed_post(row) -> FeedPost:
    p, a, s, gs, comp_name, has_emb = row[:6]
    base = post_out(p, a, bool(has_emb))
    return FeedPost(**base.model_dump(), source=SourceBrief(
        id=s.id, name=s.name or gs.title or gs.key, kind=gs.kind, role=s.role, competitor_id=s.competitor_id,
        competitor_name=comp_name))


def _encode(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()


def _decode(cursor: str | None) -> dict | None:
    if not cursor:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except ValueError as e:
        raise HTTPException(422, "Некорректный курсор") from e


SORT_KEYS = {
    "recent": func.coalesce(GlobalPost.published_at, GlobalPost.first_seen_at),
    "top": func.coalesce(GlobalPost.overperformance, -1.0),
    "er": func.coalesce(GlobalPost.er, -1.0),
}


def _key_value(sort: str, p: GlobalPost):
    """Значение ключа сортировки для курсора — так же, как его считает SORT_KEYS в SQL."""
    if sort == "recent":
        return (p.published_at or p.first_seen_at).isoformat()
    value = p.overperformance if sort == "top" else p.er
    return value if value is not None else -1.0


def _escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=FeedPage)
async def feed(
    tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session),
    role: list[SourceRole] = Query(default=[]), source_id: list[int] = Query(default=[]),
    competitor_id: list[int] = Query(default=[]), kind: list[SourceKind] = Query(default=[]),
    topic: list[str] = Query(default=[]), content_type: list[str] = Query(default=[]),
    funnel_stage: list[str] = Query(default=[]), cta_type: list[str] = Query(default=[]),
    hook_type: list[str] = Query(default=[]), media_type: list[str] = Query(default=[]),
    has_cta: bool | None = None, date_from: datetime | None = None, date_to: datetime | None = None,
    min_overperformance: float | None = None, min_er: float | None = None,
    q: str | None = Query(default=None, max_length=500), search: Literal["auto", "text", "semantic"] = "auto",
    sort: Sort = "recent", include_duplicates: bool = False, limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = None,
):
    stmt = feed_query(tenant.org_id)
    conds = []
    if not include_duplicates:
        conds.append(dedupe.not_hidden(tenant.org_id))
    for values, column in ((role, Source.role), (source_id, Source.id), (competitor_id, Source.competitor_id),
                           (kind, GlobalSource.kind), (topic, PostAnalysis.topic),
                           (content_type, PostAnalysis.content_type), (funnel_stage, PostAnalysis.funnel_stage),
                           (cta_type, PostAnalysis.cta_type), (hook_type, PostAnalysis.hook_type),
                           (media_type, GlobalPost.media_type)):
        if values:
            conds.append(column.in_(values))
    if has_cta is not None:
        conds.append(PostAnalysis.cta_type != "нет" if has_cta else PostAnalysis.cta_type == "нет")
    if date_from:
        conds.append(GlobalPost.published_at >= date_from)
    if date_to:
        conds.append(GlobalPost.published_at <= date_to)
    if min_overperformance is not None:
        conds.append(GlobalPost.overperformance >= min_overperformance)
    if min_er is not None:
        conds.append(GlobalPost.er >= min_er)

    q = (q or "").strip()
    mode, notice, distance = None, None, None
    if q:
        mode = "semantic" if search == "semantic" or (search == "auto" and settings.openrouter_api_key) else "text"
        if mode == "semantic":
            try:
                vec = await embed_query(session, get_embedding_provider(), q, org_id=tenant.org_id)
                distance = PostEmbedding.embedding.cosine_distance(vec)
                conds.append(PostEmbedding.post_id.is_not(None))
            except EmbeddingError as e:
                mode, notice = "text", f"Поиск по смыслу недоступен ({e}) — искали по словам"
        if mode == "text":
            pattern = f"%{_escape_like(q)}%"
            conds.append(or_(GlobalPost.text.ilike(pattern, escape="\\"),
                             GlobalPost.title.ilike(pattern, escape="\\")))
    if sort == "relevance" and distance is None:
        sort = "recent"  # релевантность есть только у поиска по смыслу

    cur = _decode(cursor)
    if distance is not None and sort == "relevance":
        offset = int(cur.get("o", 0)) if cur else 0
        stmt = stmt.where(*conds).add_columns(distance.label("d")).order_by(distance, GlobalPost.id.desc())
        rows = (await session.execute(stmt.offset(offset).limit(limit + 1))).all()
        next_cursor = _encode({"o": offset + limit}) if len(rows) > limit else None
    else:
        key = SORT_KEYS[sort]
        if cur:
            value = datetime.fromisoformat(cur["v"]) if sort == "recent" else float(cur["v"])
            conds.append(or_(key < value, and_(key == value, GlobalPost.id < int(cur["id"]))))
        stmt = stmt.where(*conds).order_by(key.desc(), GlobalPost.id.desc())
        rows = (await session.execute(stmt.limit(limit + 1))).all()
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1][0]
            next_cursor = _encode({"v": _key_value(sort, last), "id": last.id})
    return FeedPage(items=[feed_post(r) for r in rows[:limit]], next_cursor=next_cursor, search_mode=mode,
                    notice=notice)


async def _visible(session: AsyncSession, org_id: int, post_id: int):
    row = (await session.execute(feed_query(org_id).where(GlobalPost.id == post_id).limit(1))).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Не найдено")
    return row


@router.get("/{post_id}", response_model=PostDetail)
async def post_detail(post_id: int, tenant: Tenant = Depends(get_tenant),
                      session: AsyncSession = Depends(get_session)):
    row = await _visible(session, tenant.org_id, post_id)
    gs: GlobalSource = row[3]
    similar = []
    if row.has_emb:
        vec = (await session.execute(select(PostEmbedding.embedding).where(PostEmbedding.post_id == post_id))
               ).scalar_one()
        distance = PostEmbedding.embedding.cosine_distance(vec)
        similar = (await session.execute(
            feed_query(tenant.org_id).where(GlobalPost.id != post_id, dedupe.not_hidden(tenant.org_id),
                                       PostEmbedding.post_id.is_not(None))
            .order_by(distance).limit(5))).all()
    cached = await insights.cached(session, tenant.org_id, post_id)
    return PostDetail(post=feed_post(row), source_median_views=gs.median_views,
                      source_median_engagement=gs.median_engagement, insight=cached.data if cached else None,
                      insight_at=cached.created_at if cached else None, similar=[feed_post(r) for r in similar])


@router.post("/{post_id}/analyze", response_model=PostDetail)
async def analyze_post(post_id: int, force: bool = False, tenant: Tenant = Depends(require_role(Role.member)),
                       session: AsyncSession = Depends(get_session)):
    """AI-разбор поста. Кэшируется: повторный вызов без force=true ничего не тратит."""
    p, a, s, gs, *_ = await _visible(session, tenant.org_id, post_id)
    if force or await insights.cached(session, tenant.org_id, post_id) is None:
        if not settings.openrouter_api_key:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "OPENROUTER_API_KEY не задан — разбор недоступен")
        try:
            await insights.generate(session, tenant.org_id, p, gs, s, a)
        except AIError as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Модель не ответила: {e}") from e
    return await post_detail(post_id, tenant, session)
