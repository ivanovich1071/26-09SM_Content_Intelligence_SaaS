"""Фоновые задачи: запись в jobs + постановка в очередь arq. Воркер обновляет статус и прогресс."""
import logging

from arq.connections import ArqRedis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.models import FINAL_STATUSES, Job, JobStatus

log = logging.getLogger("sm.jobs")


async def create_job(session: AsyncSession, org_id: int, kind: str, params: dict | None = None,
                     user_id: int | None = None) -> Job:
    job = Job(organization_id=org_id, kind=kind, params=params or {}, created_by=user_id)
    session.add(job)
    await session.commit()
    return job


async def enqueue(pool: ArqRedis | None, job: Job) -> bool:
    """Ставит задачу в очередь. Без Redis задача остаётся queued — её подхватит повторная постановка."""
    if pool is None:
        log.warning("Очередь недоступна, job %s остаётся queued", job.id)
        return False
    await pool.enqueue_job(job.kind, job.id, _job_id=f"job:{job.id}")
    return True


async def set_status(session: AsyncSession, job: Job, status: JobStatus, *, progress: int | None = None,
                     result: dict | None = None, error: str | None = None) -> None:
    job.status = status
    if progress is not None:
        job.progress = progress
    if status == JobStatus.running and job.started_at is None:
        job.started_at = utcnow()
    if status in FINAL_STATUSES:
        job.finished_at = utcnow()
        if status == JobStatus.completed:
            job.progress = 100
    if result is not None:
        job.result = result
    if error is not None:
        job.error = error[:4000]
    await session.commit()
