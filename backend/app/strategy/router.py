from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import Tenant, get_tenant, require_role
from app.models import ContentAudit, ContentOpportunity, Job, Role
from app.sources.router import JobBrief
from app.strategy import opportunities

router = APIRouter(prefix="/strategy", tags=["strategy"])

VIEWS = {"active": ("new", "in_factory"), "in_factory": ("in_factory",), "done": ("done",),
         "dismissed": ("dismissed",), "archived": ("archived",)}


class GenerateIn(BaseModel):
    audit_id: int | None = None


class StatusIn(BaseModel):
    status: Literal["new", "in_factory", "done", "dismissed"]


class OpportunityOut(BaseModel):
    id: int
    rank: int
    title: str
    topic: str | None
    why: str
    angle: str
    formats: list[str]
    funnel_stage: str | None
    target_role: str | None
    fixes: list[str]
    market: dict
    examples: list[dict]
    score: float
    ai: bool
    status: str
    audit_id: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class OpportunitiesOut(BaseModel):
    items: list[OpportunityOut]
    job: JobBrief | None


async def _last_job(session: AsyncSession, org_id: int) -> Job | None:
    return (await session.execute(select(Job).where(Job.organization_id == org_id, Job.kind == "build_opportunities")
                                  .order_by(Job.id.desc()).limit(1))).scalar_one_or_none()


@router.get("/opportunities", response_model=OpportunitiesOut)
async def list_opportunities(view: Literal["active", "in_factory", "done", "dismissed", "archived"] = "active",
                             tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(tenant.scoped(select(ContentOpportunity), ContentOpportunity)
                                  .where(ContentOpportunity.status.in_(VIEWS[view]))
                                  .order_by(ContentOpportunity.job_id.desc().nulls_last(), ContentOpportunity.rank)
                                  .limit(100))).scalars().all()
    job = await _last_job(session, tenant.org_id)
    return OpportunitiesOut(items=rows, job=JobBrief.model_validate(job) if job else None)


@router.post("/opportunities/generate", response_model=JobBrief, status_code=status.HTTP_202_ACCEPTED)
async def generate(body: GenerateIn, request: Request, tenant: Tenant = Depends(require_role(Role.member)),
                   session: AsyncSession = Depends(get_session)):
    if body.audit_id is not None:
        await tenant.get(session, ContentAudit, body.audit_id)
    job = await opportunities.start(session, getattr(request.app.state, "arq", None), tenant.org_id, body.audit_id,
                                    tenant.user.id)
    if job is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Темы уже подбираются")
    return job


@router.patch("/opportunities/{opportunity_id}", response_model=OpportunityOut)
async def set_status(opportunity_id: int, body: StatusIn, tenant: Tenant = Depends(require_role(Role.member)),
                     session: AsyncSession = Depends(get_session)):
    opp = await tenant.get(session, ContentOpportunity, opportunity_id)
    opp.status = body.status
    await session.commit()
    return opp
