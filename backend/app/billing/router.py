from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import quotas
from app.core.db import get_session
from app.core.deps import Tenant, get_tenant
from app.models import LLMRequest, Plan, Subscription

router = APIRouter(prefix="/billing", tags=["billing"])


class PlanOut(BaseModel):
    code: str
    name: str
    price_month_usd: float | None
    limits: dict


class UsageOut(BaseModel):
    plan: str
    plan_until: datetime | None = None  # тариф назначен до этой даты, потом — Free
    period_start: str
    limits: dict
    used: dict[str, float]
    ai_by_operation: list[dict]


@router.get("/plans", response_model=list[PlanOut])
async def plans(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Plan).order_by(Plan.sort))).scalars()
    return [PlanOut(code=p.code, name=p.name, price_month_usd=p.price_month_usd, limits=p.limits) for p in rows]


@router.get("/usage", response_model=UsageOut)
async def usage(tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    plan, limits = await quotas.plan_limits(session, tenant.org_id)
    start = quotas.month_start()
    by_op = await session.execute(
        select(LLMRequest.operation, func.count(), func.coalesce(func.sum(LLMRequest.cost_usd), 0))
        .where(LLMRequest.organization_id == tenant.org_id, LLMRequest.at >= start)
        .group_by(LLMRequest.operation).order_by(LLMRequest.operation))
    sub = (await session.execute(select(Subscription).where(Subscription.organization_id == tenant.org_id))
           ).scalar_one_or_none()
    until = sub.current_period_end if quotas.is_active(sub) and sub.plan_code == plan else None
    return UsageOut(plan=plan, plan_until=until, period_start=start.isoformat(), limits=limits,
                    used=await quotas.month_usage(session, tenant.org_id),
                    ai_by_operation=[{"operation": op, "calls": n, "cost_usd": float(c)} for op, n, c in by_op])
