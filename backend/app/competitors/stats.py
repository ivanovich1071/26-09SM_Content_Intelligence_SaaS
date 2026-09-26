"""Статистика контента набора источников: всё считает код (медианы, доли, частота), модель только интерпретирует.
Используется в карточке конкурента и как вход Competitor Analyst."""
from collections import Counter
from datetime import timedelta
from statistics import median

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import dedupe
from app.core.db import utcnow
from app.models import GlobalPost, GlobalSource, PostAnalysis, Source

WEEKS = 12


def shares(values: list[str | None], top: int = 6) -> list[dict]:
    values = [v for v in values if v]
    if not values:
        return []
    return [{"value": v, "count": n, "share": round(100 * n / len(values), 1)}
            for v, n in Counter(values).most_common(top)]


def flag_pct(flags: list[bool | None]) -> float | None:
    flags = [f for f in flags if f is not None]
    return round(100 * sum(flags) / len(flags), 1) if flags else None


async def rows_for(session: AsyncSession, org_id: int, source_ids: list[int], days: int):
    since = utcnow() - timedelta(days=days)
    stmt = (select(GlobalPost, PostAnalysis, GlobalSource, Source)
            .join(GlobalSource, GlobalSource.id == GlobalPost.global_source_id)
            .join(Source, and_(Source.global_source_id == GlobalSource.id, Source.organization_id == org_id))
            .outerjoin(PostAnalysis, and_(PostAnalysis.post_id == GlobalPost.id,
                                          PostAnalysis.organization_id == org_id))
            .where(Source.id.in_(source_ids), GlobalPost.published_at >= since, dedupe.not_hidden(org_id))
            .order_by(GlobalPost.published_at.desc()))
    return (await session.execute(stmt)).all()


async def compute(session: AsyncSession, org_id: int, source_ids: list[int], days: int = 90) -> dict:
    return summarize(await rows_for(session, org_id, source_ids, days) if source_ids else [], days)


def summarize(rows, days: int) -> dict:
    """rows: объекты с .GlobalPost и .PostAnalysis (может быть None), отсортированы от новых к старым."""
    posts = [r.GlobalPost for r in rows]
    labels = [r.PostAnalysis for r in rows if r.PostAnalysis and not r.PostAnalysis.error]
    views = [p.views for p in posts if p.views]
    ers = [p.er for p in posts if p.er is not None]

    now = utcnow()
    week0 = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    weekly = []
    for i in range(WEEKS - 1, -1, -1):
        start, end = week0 - timedelta(weeks=i), week0 - timedelta(weeks=i - 1)
        wp = [p for p in posts if start <= p.published_at < end]
        wer = [p.er for p in wp if p.er is not None]
        weekly.append({"week": start.date().isoformat(), "posts": len(wp),
                       "median_er": round(median(wer), 2) if wer else None})
    top = sorted((r for r in rows if r.GlobalPost.overperformance), key=lambda r: r.GlobalPost.overperformance,
                 reverse=True)[:5]
    return {
        "days": days, "posts": len(posts), "analyzed": len(labels),
        "posts_per_week": round(len(posts) / (days / 7), 1),
        "median_views": median(views) if views else None,
        "median_er": round(median(ers), 2) if ers else None,
        "formats": shares([p.media_type for p in posts]),
        "topics": shares([a.topic for a in labels], top=8),
        "content_types": shares([a.content_type for a in labels]),
        "funnel": shares([a.funnel_stage for a in labels]),
        "hooks": shares([a.hook_type for a in labels if a.hook_type != "нет"]),
        "ctas": shares([a.cta_type for a in labels if a.cta_type != "нет"]),
        "tone": shares([a.tone for a in labels], top=3),
        "cta_share": flag_pct([a.cta_type != "нет" for a in labels]),
        "case_share": flag_pct([a.has_case for a in labels]),
        "numbers_share": flag_pct([a.has_numbers for a in labels]),
        "offer_share": flag_pct([a.has_offer for a in labels]),
        "lead_magnet_share": flag_pct([a.has_lead_magnet for a in labels]),
        "hook_share": flag_pct([a.hook_type != "нет" for a in labels]),
        "weekly": weekly,
        "top_post_ids": [r.GlobalPost.id for r in top],
    }
