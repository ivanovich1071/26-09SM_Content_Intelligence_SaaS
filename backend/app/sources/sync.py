"""Синхронизация источника: профиль → сбор → upsert постов → снимок метрик.

Собирается глобальный источник, поэтому канал, подключённый у многих клиентов, не качается многократно:
если его собрали меньше `source_fresh_minutes` назад, задача просто сообщает, что данные свежие."""
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import get_connector
from app.connectors.base import ContentItem
from app.connectors.http import DomainRateLimiter, Fetcher, FetchError
from app.connectors.rss import FeedError
from app.core.config import settings
from app.core.db import utcnow
from app.jobs.service import create_job, enqueue, set_status
from app.models import GlobalPost, GlobalSource, Job, JobStatus, PostMetric, Source, SourceKind, SourceStatus

log = logging.getLogger("sm.sources")

SYNC_INTERVAL = {SourceKind.telegram: timedelta(days=1), SourceKind.rss: timedelta(days=1),
                 SourceKind.website: timedelta(days=7), SourceKind.instagram: timedelta(days=7)}
BATCH = 500
KEEP_ON_UPDATE = ("first_seen_at", "global_source_id", "external_id")
ACTIVE = (JobStatus.queued, JobStatus.running, JobStatus.collecting)


class SyncError(Exception):
    """Источник недоступен или сбор упал. Текст уходит в jobs.error и виден пользователю."""
    user_facing = True


@asynccontextmanager
async def make_fetcher() -> AsyncIterator[Fetcher]:
    redis = Redis.from_url(settings.redis_url)
    try:
        async with Fetcher(DomainRateLimiter(redis)) as http:
            yield http
    finally:
        await redis.aclose()


def _row(gs_id: int, item: ContentItem, now: datetime) -> dict:
    return {
        "global_source_id": gs_id, "external_id": item.external_id, "url": item.url,
        "canonical_url": item.canonical_url, "author": item.author, "title": item.title, "text": item.text or "",
        "published_at": item.published_at, "media_type": item.media_type, "content_hash": item.content_hash,
        "links": item.links, "raw_payload": item.raw_payload, "views": item.views, "likes": item.likes,
        "comments": item.comments, "shares": item.shares, "first_seen_at": now, "metrics_at": now,
    }


async def save_items(session: AsyncSession, gs: GlobalSource, items: list[ContentItem],
                     known_ids: set[str]) -> tuple[int, int]:
    """Upsert по (источник, external_id) + снимок метрик. → (новых, обновлённых)."""
    items = list({i.external_id: i for i in items}.values())  # ON CONFLICT не терпит дублей в одной вставке
    if not items:
        return 0, 0
    now = utcnow()
    ids: dict[str, int] = {}
    for start in range(0, len(items), BATCH):
        stmt = insert(GlobalPost).values([_row(gs.id, i, now) for i in items[start:start + BATCH]])
        stmt = stmt.on_conflict_do_update(
            constraint="uq_global_posts_source_ext",
            set_={c: stmt.excluded[c] for c in _row(0, items[0], now) if c not in KEEP_ON_UPDATE},
        ).returning(GlobalPost.id, GlobalPost.external_id)
        ids.update({ext: pid for pid, ext in (await session.execute(stmt)).all()})
    snapshots = [
        {"post_id": ids[i.external_id], "taken_at": now, "views": i.views, "likes": i.likes,
         "comments": i.comments, "shares": i.shares}
        for i in items if any(v is not None for v in (i.views, i.likes, i.comments, i.shares))
    ]
    if snapshots:
        await session.execute(insert(PostMetric).values(snapshots))
    new = sum(1 for i in items if i.external_id not in known_ids)
    return new, len(items) - new


async def sync_global_source(session: AsyncSession, gs: GlobalSource, http: Fetcher, *,
                             force: bool = False) -> dict:
    now = utcnow()
    if not force and gs.status == SourceStatus.ok and gs.last_synced_at \
            and now - gs.last_synced_at < timedelta(minutes=settings.source_fresh_minutes):
        minutes = int((now - gs.last_synced_at).total_seconds() // 60)
        return {"skipped": True, "message": f"Данные свежие — источник собран {minutes} мин назад"}

    connector = get_connector(gs.kind)
    try:
        profile = await connector.get_profile(http, gs.key, gs.url, gs.meta or {})
        if not profile.available:
            gs.status, gs.last_error, gs.last_synced_at = SourceStatus.unavailable, profile.reason, now
            if profile.title:
                gs.title = profile.title
            await session.commit()
            raise SyncError(profile.reason or "Источник недоступен")
        gs.title = profile.title or gs.title
        gs.description = profile.description or gs.description
        gs.followers = profile.followers if profile.followers is not None else gs.followers
        gs.meta = {**(gs.meta or {}), **profile.meta}

        known = set((await session.execute(
            select(GlobalPost.external_id).where(GlobalPost.global_source_id == gs.id))).scalars())
        since = now - timedelta(days=settings.source_history_days)
        result = await connector.collect(http, gs.key, gs.url, gs.meta, since=since, known_ids=known)
    except (FetchError, FeedError) as e:
        gs.status, gs.last_error = SourceStatus.error, str(e)
        await session.commit()
        raise SyncError(str(e)) from e

    new, updated = await save_items(session, gs, result.items, known)
    gs.status, gs.last_error, gs.last_synced_at = SourceStatus.ok, None, now
    await session.commit()
    out = {"skipped": False, "posts_new": new, "posts_updated": updated, "pages": result.pages,
           "followers": gs.followers}
    if gs.kind == SourceKind.website and not gs.meta.get("feed_url"):
        out["message"] = ("На сайте не найден RSS/Atom блога — статьи не собираются. "
                          "Изменения страниц — во вкладке «Сайты».")
    log.info("sync %s:%s → %s", gs.kind, gs.key, out)
    return out


async def handle_sync_source(session: AsyncSession, job: Job) -> dict:
    source = await session.get(Source, job.params.get("source_id"))
    if source is None or source.organization_id != job.organization_id:
        raise SyncError("Источник удалён")
    await set_status(session, job, JobStatus.collecting, progress=10)
    async with make_fetcher() as http:
        return await sync_global_source(session, source.global_source, http, force=job.params.get("force", False))


async def active_job(session: AsyncSession, org_id: int, source_id: int) -> Job | None:
    stmt = (select(Job).where(Job.organization_id == org_id, Job.kind == "sync_source", Job.status.in_(ACTIVE),
                              Job.params["source_id"].as_integer() == source_id)
            .order_by(Job.id.desc()).limit(1))
    return (await session.execute(stmt)).scalar_one_or_none()


async def schedule_due(session: AsyncSession, pool) -> int:
    """Для cron: один sync на каждый просроченный глобальный источник (от имени первой подключившей организации)."""
    now = utcnow()
    rows = (await session.execute(
        select(Source, GlobalSource).join(GlobalSource).where(Source.enabled.is_(True)).order_by(Source.id)
    )).all()
    seen, created = set(), 0
    for source, gs in rows:
        if gs.id in seen:
            continue
        seen.add(gs.id)
        if gs.last_synced_at and now - gs.last_synced_at < SYNC_INTERVAL[gs.kind]:
            continue
        if await active_job(session, source.organization_id, source.id):
            continue
        job = await create_job(session, source.organization_id, "sync_source", {"source_id": source.id})
        await enqueue(pool, job)
        created += 1
    return created
