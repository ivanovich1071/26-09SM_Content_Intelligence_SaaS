"""Задачи воркера. Каждая получает job_id, сама переводит job по статусам и не падает молча."""
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import pipeline
from app.audits import pipeline as audit_pipeline
from app.competitors import profile
from app.core.db import SessionLocal
from app.factory import pipeline as factory_pipeline
from app.jobs.service import set_status
from app.models import Job, JobStatus
from app.sources import sync
from app.strategy import opportunities
from app.topics import cluster
from app.websites import crawl

log = logging.getLogger("sm.worker")

Handler = Callable[[AsyncSession, Job], Awaitable[dict]]


async def run_job(job_id: int, handler: Handler) -> None:
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status == JobStatus.cancelled:
            return
        await set_status(session, job, JobStatus.running)
        try:
            result = await handler(session, job)
        except Exception as e:  # noqa: BLE001 — любая ошибка задачи фиксируется в jobs
            log.exception("job %s (%s) упал", job_id, job.kind)
            await session.rollback()
            # Ошибки с user_facing=True уже сформулированы для пользователя — без имени класса
            message = str(e) if getattr(e, "user_facing", False) else f"{type(e).__name__}: {e}"
            await set_status(session, job, JobStatus.failed, error=message)
            return
        await session.refresh(job)
        if job.status != JobStatus.cancelled:
            await set_status(session, job, JobStatus.completed, result=result)


async def _ping(session: AsyncSession, job: Job) -> dict:
    return {"pong": True, "organization_id": job.organization_id}


async def ping(ctx: dict, job_id: int) -> None:
    await run_job(job_id, _ping)


async def sync_source(ctx: dict, job_id: int) -> None:
    await run_job(job_id, sync.handle_sync_source)
    await pipeline.after_sync(job_id, ctx.get("redis"))


async def analyze_source(ctx: dict, job_id: int) -> None:
    await run_job(job_id, pipeline.handle_analyze_source)
    await profile.after_analysis(job_id, ctx.get("redis"))


async def profile_competitor(ctx: dict, job_id: int) -> None:
    await run_job(job_id, profile.handle_profile_competitor)


async def schedule_syncs(ctx: dict) -> None:
    """Cron: Telegram и RSS — раз в сутки, сайты и Instagram — раз в неделю (см. sync.SYNC_INTERVAL)."""
    async with SessionLocal() as session:
        created = await sync.schedule_due(session, ctx.get("redis"))
    if created:
        log.info("Запланировано синхронизаций: %s", created)


async def cluster_topics(ctx: dict, job_id: int) -> None:
    await run_job(job_id, cluster.handle_cluster_topics)


async def schedule_clustering(ctx: dict) -> None:
    """Cron: раз в сутки — под-темы организаций, у которых они старше недели или ещё не считались."""
    async with SessionLocal() as session:
        created = await cluster.schedule_due(session, ctx.get("redis"))
    if created:
        log.info("Запланирован пересчёт под-тем: %s", created)


async def crawl_website(ctx: dict, job_id: int) -> None:
    await run_job(job_id, crawl.handle_crawl_website)


async def schedule_crawls(ctx: dict) -> None:
    """Cron: сайты, обход которых старше website_crawl_days."""
    async with SessionLocal() as session:
        created = await crawl.schedule_due(session, ctx.get("redis"))
    if created:
        log.info("Запланировано обходов сайтов: %s", created)


async def run_audit(ctx: dict, job_id: int) -> None:
    await run_job(job_id, audit_pipeline.handle_run_audit)
    await opportunities.after_audit(job_id, ctx.get("redis"))


async def build_opportunities(ctx: dict, job_id: int) -> None:
    await run_job(job_id, opportunities.handle_build_opportunities)


async def generate_content(ctx: dict, job_id: int) -> None:
    await run_job(job_id, factory_pipeline.handle_generate_content)
