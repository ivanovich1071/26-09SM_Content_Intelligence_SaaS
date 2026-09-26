from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import Tenant, get_tenant, require_role
from app.jobs import service
from app.models import FINAL_STATUSES, Job, JobStatus, Role

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobOut(BaseModel):
    id: int
    kind: str
    status: JobStatus
    progress: int
    params: dict
    result: dict | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


@router.get("", response_model=list[JobOut])
async def list_jobs(limit: int = 50, tenant: Tenant = Depends(get_tenant),
                    session: AsyncSession = Depends(get_session)):
    stmt = tenant.scoped(select(Job), Job).order_by(Job.id.desc()).limit(min(limit, 200))
    return list((await session.execute(stmt)).scalars())


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: int, tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    return await tenant.get(session, Job, job_id)


@router.post("/ping", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
async def ping(request: Request, tenant: Tenant = Depends(require_role(Role.member)),
               session: AsyncSession = Depends(get_session)):
    """Проверочная задача: проходит через очередь и воркер, ничего не тратит."""
    job = await service.create_job(session, tenant.org_id, "ping", user_id=tenant.user.id)
    await service.enqueue(getattr(request.app.state, "arq", None), job)
    return job


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel(job_id: int, tenant: Tenant = Depends(require_role(Role.member)),
                 session: AsyncSession = Depends(get_session)):
    job: Job = await tenant.get(session, Job, job_id)
    if job.status in FINAL_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Задача уже завершена")
    await service.set_status(session, job, JobStatus.cancelled)
    return job
