"""Админка сервиса (EPIC 13): пользователи, организации и тарифы, расход LLM, задачи, ошибки, провайдеры.

Доступ — только суперадминам; каждое изменение пишется в admin_actions."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import stats
from app.admin.deps import get_superadmin, log_action
from app.billing.plans import PLANS
from app.billing.quotas import METRIC_LABELS
from app.connectors import CONNECTORS, KEYS_NEEDED
from app.core.config import settings
from app.core.db import get_session, utcnow
from app.digests import mailer
from app.jobs import service as jobs
from app.models import FINAL_STATUSES, Job, JobStatus, Organization, Subscription, User

router = APIRouter(prefix="/admin", tags=["admin"])
WORKER_HEALTH_KEY = "arq:queue:health-check"  # arq пишет его раз в час (TTL = интервал)
STATUSES = ("active", "past_due", "cancelled")


def _page(page: int, per_page: int) -> tuple[int, int]:
    return max(page, 1), min(max(per_page, 1), 100)


@router.get("/overview")
async def overview(days: int = 30, _: User = Depends(get_superadmin), session: AsyncSession = Depends(get_session)):
    return await stats.overview(session, min(max(days, 7), 90))


# --- организации и подписки ---

@router.get("/organizations")
async def organizations(q: str = "", plan: str = "", sort: str = "created", page: int = 1, per_page: int = 50,
                        _: User = Depends(get_superadmin), session: AsyncSession = Depends(get_session)):
    page, per_page = _page(page, per_page)
    return await stats.organizations(session, q=q, plan=plan, sort=sort, page=page, per_page=per_page)


async def _org(session: AsyncSession, org_id: int) -> Organization:
    org = await session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Организация не найдена")
    return org


@router.get("/organizations/{org_id}")
async def organization(org_id: int, _: User = Depends(get_superadmin), session: AsyncSession = Depends(get_session)):
    return await stats.organization(session, await _org(session, org_id))


class SubscriptionIn(BaseModel):
    plan_code: str
    status: str = "active"
    current_period_end: datetime | None = None  # пусто — бессрочно
    limits_override: dict[str, float | None] | None = None  # метрика → лимит (null — без ограничения)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("plan_code")
    @classmethod
    def known_plan(cls, v: str) -> str:
        if v not in PLANS:
            raise ValueError(f"Неизвестный тариф: {v}")
        return v

    @field_validator("status")
    @classmethod
    def known_status(cls, v: str) -> str:
        if v not in STATUSES:
            raise ValueError(f"Статус: {', '.join(STATUSES)}")
        return v

    @field_validator("limits_override")
    @classmethod
    def known_limits(cls, v: dict | None) -> dict | None:
        if not v:
            return None
        unknown = set(v) - set(METRIC_LABELS)
        if unknown:
            raise ValueError(f"Неизвестные лимиты: {', '.join(sorted(unknown))}")
        if any(x is not None and x < 0 for x in v.values()):
            raise ValueError("Лимит не может быть отрицательным")
        return v


@router.put("/organizations/{org_id}/subscription")
async def put_subscription(org_id: int, body: SubscriptionIn, admin: User = Depends(get_superadmin),
                           session: AsyncSession = Depends(get_session)):
    org = await _org(session, org_id)
    sub = (await session.execute(select(Subscription).where(Subscription.organization_id == org.id))
           ).scalar_one_or_none()
    before = None
    if sub is None:
        sub = Subscription(organization_id=org.id, plan_code=body.plan_code)
        session.add(sub)
    else:
        before = {"plan_code": sub.plan_code, "status": sub.status,
                  "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
                  "limits_override": sub.limits_override}
        if sub.plan_code != body.plan_code:
            sub.current_period_start = utcnow()
    sub.plan_code, sub.status, sub.current_period_end = body.plan_code, body.status, body.current_period_end
    sub.limits_override, sub.note = body.limits_override, body.note
    after = {"plan_code": body.plan_code, "status": body.status,
             "current_period_end": body.current_period_end.isoformat() if body.current_period_end else None,
             "limits_override": body.limits_override}
    log_action(session, admin, "subscription.update", "organization", org.id, org_id=org.id, before=before,
               after=after)
    await session.commit()
    await session.refresh(sub, ["plan"])  # связь plan не следует за plan_code сама
    return await stats.subscription_out(session, org.id)


# --- пользователи ---

@router.get("/users")
async def users(q: str = "", flag: str = "", page: int = 1, per_page: int = 50, _: User = Depends(get_superadmin),
                session: AsyncSession = Depends(get_session)):
    page, per_page = _page(page, per_page)
    return await stats.users(session, q=q, flag=flag, page=page, per_page=per_page)


class UserPatch(BaseModel):
    is_active: bool | None = None
    is_superadmin: bool | None = None


@router.patch("/users/{user_id}")
async def patch_user(user_id: int, body: UserPatch, admin: User = Depends(get_superadmin),
                     session: AsyncSession = Depends(get_session)):
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    if user.id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Свой доступ менять нельзя — попросите другого суперадмина")
    changes = {k: v for k, v in body.model_dump(exclude_none=True).items() if getattr(user, k) != v}
    for k, v in changes.items():
        setattr(user, k, v)
    if changes:
        log_action(session, admin, "user.update", "user", user.id, email=user.email, **changes)
        await session.commit()
    return stats.user_out(user, [])


# --- расходы, задачи, ошибки ---

@router.get("/costs")
async def costs(days: int = 30, _: User = Depends(get_superadmin), session: AsyncSession = Depends(get_session)):
    return await stats.costs(session, min(max(days, 1), 365))


@router.get("/jobs")
async def job_list(status: str = "", kind: str = "", org_id: int | None = None, page: int = 1, per_page: int = 50,
                   _: User = Depends(get_superadmin), session: AsyncSession = Depends(get_session)):
    page, per_page = _page(page, per_page)
    return await stats.jobs(session, status=status, kind=kind, org_id=org_id, page=page, per_page=per_page)


async def _job(session: AsyncSession, job_id: int) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена")
    return job


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: int, admin: User = Depends(get_superadmin), session: AsyncSession = Depends(get_session)):
    job = await _job(session, job_id)
    if job.status in FINAL_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Задача уже завершена")
    log_action(session, admin, "job.cancel", "job", job.id, org_id=job.organization_id, kind=job.kind)
    await jobs.set_status(session, job, JobStatus.cancelled, error="Отменена администратором")
    return stats.job_out(job)


@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_job(job_id: int, request: Request, admin: User = Depends(get_superadmin),
                    session: AsyncSession = Depends(get_session)):
    job = await _job(session, job_id)
    if job.status not in (JobStatus.failed, JobStatus.cancelled):
        raise HTTPException(status.HTTP_409_CONFLICT, "Повторить можно только упавшую или отменённую задачу")
    if job.kind not in stats.RETRYABLE:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Задачу «{job.kind}» повторяет пользователь из интерфейса — она привязана к своей записи")
    new = await jobs.create_job(session, job.organization_id, job.kind, dict(job.params), user_id=admin.id)
    queued = await jobs.enqueue(getattr(request.app.state, "arq", None), new)
    log_action(session, admin, "job.retry", "job", job.id, org_id=job.organization_id, kind=job.kind, new_job=new.id)
    await session.commit()
    return {**stats.job_out(new), "queued": queued}


@router.get("/errors")
async def errors(days: int = 7, kind: str = "", _: User = Depends(get_superadmin),
                 session: AsyncSession = Depends(get_session)):
    return await stats.errors(session, min(max(days, 1), 90), kind)


# --- провайдеры ---

async def _redis_state() -> tuple[str, bool]:
    try:
        redis = Redis.from_url(settings.redis_url)
        try:
            worker = bool(await redis.exists(WORKER_HEALTH_KEY))
        finally:
            await redis.aclose()
        return "ok", worker
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}", False


@router.get("/providers")
async def providers(request: Request, _: User = Depends(get_superadmin), session: AsyncSession = Depends(get_session)):
    redis, worker = await _redis_state()
    health = await stats.connector_health(session)
    connectors = []
    for kind in CONNECTORS:
        key = KEYS_NEEDED.get(kind)
        connectors.append({"kind": kind, "key_setting": key.upper() if key else None,
                           "configured": not key or bool(getattr(settings, key)),
                           **health.get(kind, {"sources": 0, "errors": 0, "unavailable": 0, "last_synced_at": None})})
    return {
        "llm": {"provider": "openrouter", "configured": bool(settings.openrouter_api_key),
                "base_url": settings.openrouter_base_url,
                "models": {"analyze": settings.llm_model_analyze, "classify": settings.llm_model_classify,
                           "write": settings.llm_model_write, "qa": settings.llm_model_qa},
                "embedding": {"provider": settings.embedding_provider, "model": settings.embedding_model,
                              "dim": settings.embedding_dim},
                "health_24h": await stats.llm_health(session)},
        "connectors": connectors,
        "email": {"configured": mailer.configured(), "host": settings.smtp_host or None,
                  "from": settings.smtp_from or None},
        "captcha": {"configured": bool(settings.turnstile_secret)},
        "infra": {"redis": redis, "queue": getattr(request.app.state, "arq", None) is not None, "worker": worker},
    }


@router.get("/actions")
async def actions(page: int = 1, per_page: int = 50, _: User = Depends(get_superadmin),
                  session: AsyncSession = Depends(get_session)):
    page, per_page = _page(page, per_page)
    return await stats.actions(session, page=page, per_page=per_page)
