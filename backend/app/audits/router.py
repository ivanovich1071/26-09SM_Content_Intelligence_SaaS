from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audits import pipeline, public
from app.audits.criteria import BY_KEY
from app.billing import quotas, usage
from app.connectors import InvalidSource
from app.connectors.base import normalize_web_url
from app.core.db import get_session
from app.core.deps import Tenant, get_tenant, require_role
from app.models import AuditItem, ContentAudit, Job, JobStatus, Organization, Role, Source, SourceRole
from app.sources import service

router = APIRouter(prefix="/audits", tags=["audits"])
public_router = APIRouter(prefix="/public/audits", tags=["public"])

RUNNING = {JobStatus.running, JobStatus.collecting, JobStatus.analyzing}


class AuditIn(BaseModel):
    company: str | None = Field(default=None, max_length=200)
    website: str | None = Field(default=None, max_length=1000)
    sources: list[str] = Field(default=[], max_length=10)
    use_own_sources: bool = True


class PublicAuditIn(BaseModel):
    company: str = Field(min_length=1, max_length=200)
    website: str | None = Field(default=None, max_length=1000)
    sources: list[str] = Field(default=[], max_length=5)
    email: EmailStr | None = None
    captcha_token: str | None = Field(default=None, max_length=4000)
    hp: str | None = None  # скрытое поле-ловушка для ботов


class ClaimIn(BaseModel):
    token: str = Field(min_length=10, max_length=64)


class ItemOut(BaseModel):
    criterion: str
    name: str
    weight: float
    score: float | None
    explanation: str
    evidence: list[dict]
    recommendations: list[str]
    ai: bool
    locked: bool = False


class AuditBrief(BaseModel):
    id: int
    job_id: int | None
    company: str
    website: str | None
    score: float | None
    status: str       # queued | running | completed | failed
    stage: str
    progress: int
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class AuditOut(AuditBrief):
    inputs: list[str]
    use_own_sources: bool
    model: str | None
    result: dict
    items: list[ItemOut]
    locked: bool = False
    token: str | None = None


def _status(job: Job | None) -> str:
    if job is None or job.status == JobStatus.queued:
        return "queued"
    if job.status in RUNNING:
        return "running"
    return "completed" if job.status == JobStatus.completed else "failed"


def _brief(a: ContentAudit, job: Job | None) -> dict:
    return dict(id=a.id, job_id=a.job_id, company=a.company, website=a.website, score=a.score, status=_status(job),
                stage=a.stage, progress=job.progress if job else 0, error=job.error if job else None,
                created_at=a.created_at, finished_at=a.finished_at)


async def _full(session: AsyncSession, a: ContentAudit) -> dict:
    job = await session.get(Job, a.job_id) if a.job_id else None
    rows = await session.execute(select(AuditItem).where(AuditItem.audit_id == a.id).order_by(AuditItem.sort))
    items = [dict(criterion=i.criterion, name=BY_KEY[i.criterion].name, weight=i.weight, score=i.score,
                  explanation=i.explanation, evidence=i.evidence or [], recommendations=i.recommendations or [],
                  ai=i.ai) for i in rows.scalars()]
    return {**_brief(a, job), "inputs": a.inputs or [], "use_own_sources": a.use_own_sources, "model": a.model,
            "result": a.result or {}, "items": items}


def _validate(website: str | None, sources: list[str]) -> tuple[str | None, list[str]]:
    try:
        site = normalize_web_url(website) if website and website.strip() else None
        urls = [url for _, _, url in (service.resolve(s) for s in sources if s.strip())]
    except InvalidSource as e:
        raise HTTPException(422, str(e)) from e
    return site, list(dict.fromkeys(urls))


@router.get("", response_model=list[AuditBrief])
async def list_audits(tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    rows = await session.execute(tenant.scoped(select(ContentAudit, Job), ContentAudit)
                                 .outerjoin(Job, Job.id == ContentAudit.job_id)
                                 .order_by(ContentAudit.id.desc()).limit(100))
    return [_brief(a, j) for a, j in rows.all()]


@router.post("", response_model=AuditOut, status_code=status.HTTP_201_CREATED)
async def create_audit(body: AuditIn, request: Request, tenant: Tenant = Depends(require_role(Role.member)),
                       session: AsyncSession = Depends(get_session)):
    site, urls = _validate(body.website, body.sources)
    own = 0
    if body.use_own_sources:
        own = (await session.execute(select(func.count()).select_from(Source).where(
            Source.organization_id == tenant.org_id, Source.role == SourceRole.own,
            Source.enabled.is_(True)))).scalar_one()
    if not site and not urls and not own:
        raise HTTPException(422, "Укажите сайт или хотя бы один канал компании — или отметьте свои каналы "
                                 "ролью «свой» в «Источниках».")
    await quotas.check(session, tenant.org_id, "audits_month")
    company = (body.company or "").strip() or (await session.get(Organization, tenant.org_id)).name
    audit = ContentAudit(organization_id=tenant.org_id, company=company, website=site, inputs=urls,
                         use_own_sources=body.use_own_sources, created_by=tenant.user.id)
    session.add(audit)
    await session.flush()
    await usage.record(session, tenant.org_id, "audits", "content_audit", commit=False)
    await session.commit()
    await pipeline.start(session, getattr(request.app.state, "arq", None), audit, tenant.user.id)
    return await _full(session, audit)


@router.post("/claim", response_model=AuditOut)
async def claim(body: ClaimIn, tenant: Tenant = Depends(require_role(Role.member)),
                session: AsyncSession = Depends(get_session)):
    """Бесплатный аудит, сделанный до регистрации, переходит в организацию пользователя — с полным отчётом."""
    audit = (await session.execute(select(ContentAudit).where(
        ContentAudit.public_token == body.token, ContentAudit.is_public.is_(True)))).scalar_one_or_none()
    if audit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Аудит не найден или уже перенесён")
    audit.organization_id, audit.is_public, audit.public_token = tenant.org_id, False, None
    audit.created_by = tenant.user.id
    await session.commit()
    return await _full(session, audit)


@router.get("/{audit_id}", response_model=AuditOut)
async def get_audit(audit_id: int, tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    return await _full(session, await tenant.get(session, ContentAudit, audit_id))


@router.delete("/{audit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_audit(audit_id: int, tenant: Tenant = Depends(require_role(Role.member)),
                       session: AsyncSession = Depends(get_session)):
    await session.delete(await tenant.get(session, ContentAudit, audit_id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@public_router.post("", response_model=AuditOut, status_code=status.HTTP_201_CREATED)
async def create_public(body: PublicAuditIn, request: Request, session: AsyncSession = Depends(get_session)):
    if body.hp:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Запрос отклонён")
    site, urls = _validate(body.website, body.sources)
    if not site and not urls:
        raise HTTPException(422, "Укажите сайт или хотя бы один канал компании")
    ip = public.client_ip(request)
    await public.verify_captcha(body.captcha_token, ip)
    org = await public.organization(session)
    email = body.email.lower() if body.email else None
    ip_h = public.ip_hash(ip)
    await public.check_limits(session, org.id, ip_h, email)
    audit = ContentAudit(organization_id=org.id, company=body.company.strip(), website=site, inputs=urls,
                         is_public=True, public_token=public.new_token(), ip_hash=ip_h, email=email)
    session.add(audit)
    await session.flush()
    await usage.record(session, org.id, "audits", "public_audit", commit=False)
    await session.commit()
    await pipeline.start(session, getattr(request.app.state, "arq", None), audit)
    return {**public.redact(await _full(session, audit)), "token": audit.public_token}


@public_router.get("/{token}", response_model=AuditOut)
async def get_public(token: str, session: AsyncSession = Depends(get_session)):
    audit = (await session.execute(select(ContentAudit).where(
        ContentAudit.public_token == token, ContentAudit.is_public.is_(True)))).scalar_one_or_none()
    if audit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Аудит не найден — возможно, он уже перенесён в аккаунт")
    return {**public.redact(await _full(session, audit)), "token": token}
