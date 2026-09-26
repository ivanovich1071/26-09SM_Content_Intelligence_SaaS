"""AI-разбор поста (агент post_analysis). Метрики и медианы источника считает код — модель их интерпретирует."""
import json

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import prompts
from app.ai.router import AIRouter, model_for
from app.analysis import taxonomy
from app.core.db import utcnow
from app.models import GlobalPost, GlobalSource, PostAnalysis, PostInsight, Source


class Insight(BaseModel):
    summary: str
    hook: str = ""
    pain_point: str = ""
    audience: str = ""
    argumentation: str = ""
    cta: str = ""
    format_notes: str = ""
    why_it_worked: str = ""
    patterns_to_use: list[str] = []
    do_not_copy: list[str] = []


async def cached(session: AsyncSession, org_id: int, post_id: int) -> PostInsight | None:
    return (await session.execute(select(PostInsight).where(
        PostInsight.organization_id == org_id, PostInsight.post_id == post_id)
        .execution_options(populate_existing=True))).scalar_one_or_none()  # после upsert — свежие данные


def user_prompt(post: GlobalPost, gs: GlobalSource, source: Source, analysis: PostAnalysis | None) -> str:
    return json.dumps({
        "источник": {"название": source.name or gs.title or gs.key, "площадка": gs.kind.value,
                     "роль": source.role.value, "подписчики": gs.followers,
                     "медиана_просмотров": gs.median_views, "медиана_вовлечённости": gs.median_engagement},
        "публикация": {"дата": post.published_at.isoformat() if post.published_at else None,
                       "формат": post.media_type, "заголовок": post.title, "текст": (post.text or "")[:4000]},
        "метрики": {"просмотры": post.views, "реакции": post.likes, "комментарии": post.comments,
                    "репосты": post.shares, "er_процент": post.er, "overperformance": post.overperformance},
        "разметка": {k: getattr(analysis, k) for k in (
            "topic", "target_role", "content_type", "funnel_stage", "hook_type", "cta_type", "proof_type", "tone")}
        if analysis and not analysis.error else None,
    }, ensure_ascii=False)


async def generate(session: AsyncSession, org_id: int, post: GlobalPost, gs: GlobalSource, source: Source,
                   analysis: PostAnalysis | None, router: AIRouter | None = None) -> PostInsight:
    tax = await taxonomy.get(session, org_id)
    data = await (router or AIRouter(session)).run(
        "analyze", prompts.load("post_analysis/system").replace("{niche}", tax.niche),
        user_prompt(post, gs, source, analysis), org_id=org_id, operation="post_insight", schema=Insight,
        max_tokens=1500)
    stmt = insert(PostInsight).values(organization_id=org_id, post_id=post.id, data=data, model=model_for("analyze"),
                                      created_at=utcnow())
    await session.execute(stmt.on_conflict_do_update(
        constraint="uq_post_insights_org_post",
        set_={"data": stmt.excluded.data, "model": stmt.excluded.model, "created_at": stmt.excluded.created_at}))
    await session.commit()
    return await cached(session, org_id, post.id)
