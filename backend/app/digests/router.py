from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session, utcnow
from app.core.deps import Tenant, get_tenant, require_role
from app.digests import generator, mailer, render
from app.models import Digest, DigestSchedule, Job, Organization, Role
from app.sources.router import JobBrief

router = APIRouter(prefix="/digests", tags=["digests"])


class GenerateIn(BaseModel):
    days: int = Field(default=7, ge=1, le=90)
    recipients: list[EmailStr] = Field(default=[], max_length=20)


class SendIn(BaseModel):
    recipients: list[EmailStr] = Field(min_length=1, max_length=20)


class ScheduleIn(BaseModel):
    enabled: bool = False
    period: Literal["weekly", "monthly", "custom"] = "weekly"
    weekday: int = Field(default=0, ge=0, le=6)
    day: int = Field(default=1, ge=1, le=28)
    every_days: int = Field(default=14, ge=1, le=90)
    hour: int = Field(default=6, ge=0, le=23)
    send_email: bool = False
    recipients: list[EmailStr] = Field(default=[], max_length=20)


class ScheduleOut(ScheduleIn):
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    email_configured: bool


class DigestBrief(BaseModel):
    id: int
    title: str
    headline: str
    period_from: datetime
    period_to: datetime
    days: int
    trigger: str
    ai: bool
    emailed_at: datetime | None
    email_error: str | None
    created_at: datetime


class DigestOut(DigestBrief):
    stats: dict
    sections: dict
    model: str | None


class DigestList(BaseModel):
    items: list[DigestBrief]
    job: JobBrief | None


def _brief(org: Organization, d: Digest) -> dict:
    return dict(id=d.id, title=generator.title(org, d), headline=(d.sections or {}).get("headline", ""),
                period_from=d.period_from, period_to=d.period_to, days=d.days, trigger=d.trigger, ai=d.ai,
                emailed_at=d.emailed_at, email_error=d.email_error, created_at=d.created_at)


async def _schedule(session: AsyncSession, org_id: int) -> DigestSchedule | None:
    return (await session.execute(select(DigestSchedule).where(DigestSchedule.organization_id == org_id))
            ).scalar_one_or_none()


def _schedule_out(s: DigestSchedule | None) -> dict:
    base = ScheduleIn().model_dump() if s is None else {k: getattr(s, k) for k in ScheduleIn.model_fields}
    return {**base, "last_run_at": s.last_run_at if s else None, "next_run_at": s.next_run_at if s else None,
            "email_configured": mailer.configured()}


@router.get("", response_model=DigestList)
async def list_digests(tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    org = await session.get(Organization, tenant.org_id)
    rows = (await session.execute(tenant.scoped(select(Digest), Digest).order_by(Digest.id.desc()).limit(100)
                                  )).scalars().all()
    job = (await session.execute(select(Job).where(Job.organization_id == tenant.org_id, Job.kind == "generate_digest")
                                 .order_by(Job.id.desc()).limit(1))).scalar_one_or_none()
    return DigestList(items=[_brief(org, d) for d in rows], job=JobBrief.model_validate(job) if job else None)


@router.post("/generate", response_model=JobBrief, status_code=status.HTTP_202_ACCEPTED)
async def generate(body: GenerateIn, request: Request, tenant: Tenant = Depends(require_role(Role.member)),
                   session: AsyncSession = Depends(get_session)):
    if body.recipients and not mailer.configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Email не настроен: задайте SMTP_HOST и SMTP_FROM")
    job = await generator.start(session, getattr(request.app.state, "arq", None), tenant.org_id, body.days,
                                recipients=[str(r).lower() for r in body.recipients], user_id=tenant.user.id)
    if job is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Дайджест уже собирается")
    return job


@router.get("/schedule", response_model=ScheduleOut)
async def get_schedule(tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    return _schedule_out(await _schedule(session, tenant.org_id))


@router.put("/schedule", response_model=ScheduleOut)
async def put_schedule(body: ScheduleIn, tenant: Tenant = Depends(require_role(Role.admin)),
                       session: AsyncSession = Depends(get_session)):
    if body.enabled and body.send_email and not body.recipients:
        raise HTTPException(422, "Укажите получателей письма")
    s = await _schedule(session, tenant.org_id)
    if s is None:
        s = DigestSchedule(organization_id=tenant.org_id)
        session.add(s)
    for k, v in body.model_dump().items():
        setattr(s, k, [str(x).lower() for x in v] if k == "recipients" else v)
    s.next_run_at = generator.next_run(s, utcnow()) if s.enabled else None
    await session.commit()
    return _schedule_out(s)


@router.get("/{digest_id}", response_model=DigestOut)
async def get_digest(digest_id: int, tenant: Tenant = Depends(get_tenant),
                     session: AsyncSession = Depends(get_session)):
    d = await tenant.get(session, Digest, digest_id)
    org = await session.get(Organization, tenant.org_id)
    return {**_brief(org, d), "stats": d.stats, "sections": d.sections, "model": d.model}


@router.get("/{digest_id}/export")
async def export(digest_id: int, type: Literal["md", "html"] = "md", tenant: Tenant = Depends(get_tenant),
                 session: AsyncSession = Depends(get_session)):
    d = await tenant.get(session, Digest, digest_id)
    t = generator.title(await session.get(Organization, tenant.org_id), d)
    if type == "md":
        body, media = render.markdown(t, d.stats, d.sections), "text/markdown"
    else:
        url = f"{settings.app_url.rstrip('/')}/digest/{d.id}"
        body, media = render.email_html(t, d.stats, d.sections, url), "text/html"
    return Response(body, media_type=f"{media}; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="digest-{d.id}.{type}"'})


@router.post("/{digest_id}/send", response_model=DigestBrief)
async def send(digest_id: int, body: SendIn, tenant: Tenant = Depends(require_role(Role.member)),
               session: AsyncSession = Depends(get_session)):
    d = await tenant.get(session, Digest, digest_id)
    if not mailer.configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Email не настроен: задайте SMTP_HOST и SMTP_FROM")
    try:
        await generator.send_email(session, d, [str(r).lower() for r in body.recipients])
    except mailer.MailError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    return _brief(await session.get(Organization, tenant.org_id), d)


@router.delete("/{digest_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(digest_id: int, tenant: Tenant = Depends(require_role(Role.member)),
                 session: AsyncSession = Depends(get_session)):
    await session.delete(await tenant.get(session, Digest, digest_id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
