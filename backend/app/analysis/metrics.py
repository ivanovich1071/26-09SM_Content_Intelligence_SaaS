"""Метрики постов и базовая линия источника. Перенос логики VM_SM app/analytics/metrics.py (er_view, lift к медиане).

Числа считает код: ER = (реакции + комментарии + репосты) / просмотры, %.
overperformance — во сколько раз пост лучше медианы своего источника за 90 дней: по просмотрам, а где их нет
(Instagram-фото, часть площадок) — по вовлечённости. Так маленький и большой канал сравнимы между собой."""
from datetime import timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.models import GlobalPost, GlobalSource

WINDOW = timedelta(days=90)
MIN_BASELINE = 3  # меньше постов — медиана случайна, overperformance не считаем


def engagement(p: GlobalPost) -> int | None:
    parts = [p.likes, p.comments, p.shares]
    return sum(v or 0 for v in parts) if any(v is not None for v in parts) else None


def er(p: GlobalPost, eng: int | None) -> float | None:
    return round(100 * eng / p.views, 3) if p.views and eng is not None else None


def _median(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return float(median(values)) if len(values) >= MIN_BASELINE else None


async def calculate(session: AsyncSession, gs: GlobalSource) -> dict:
    now = utcnow()
    posts = list((await session.execute(
        select(GlobalPost).where(GlobalPost.global_source_id == gs.id))).scalars())
    for p in posts:
        p.engagement = engagement(p)
        p.er = er(p, p.engagement)
    recent = [p for p in posts if p.published_at is None or p.published_at >= now - WINDOW]
    gs.median_views = _median([p.views for p in recent if p.views])
    gs.median_engagement = _median([p.engagement for p in recent if p.engagement])
    gs.metrics_at = now
    scored = 0
    for p in posts:
        if gs.median_views and p.views:
            p.overperformance = round(p.views / gs.median_views, 3)
        elif gs.median_engagement and p.engagement is not None and not p.views:
            p.overperformance = round(p.engagement / gs.median_engagement, 3)
        else:
            p.overperformance = None
        scored += p.overperformance is not None
    await session.commit()
    return {"posts": len(posts), "with_overperformance": scored, "median_views": gs.median_views,
            "median_engagement": gs.median_engagement}
