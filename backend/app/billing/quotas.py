from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.plans import DEFAULT_PLAN, PLANS
from app.models import Subscription, UsageEvent

METRIC_LABELS = {
    "members": "участники", "competitors": "конкуренты", "sources": "источники", "websites": "сайты",
    "website_pages": "страниц на сайт",
    "audits_month": "аудиты в месяц", "generations_month": "генерации в месяц",
    "ai_cost_usd_month": "расход AI в месяц, $",
}


class QuotaExceeded(HTTPException):
    def __init__(self, metric: str, used: float, limit: float):
        label = METRIC_LABELS.get(metric, metric)
        super().__init__(status.HTTP_402_PAYMENT_REQUIRED,
                         detail={"code": "quota_exceeded", "metric": metric, "used": used, "limit": limit,
                                 "message": f"Лимит тарифа «{label}» исчерпан ({used:g} из {limit:g}). "
                                            f"Повысьте тариф в «Настройки → Тариф»."})
        self.metric = metric


def month_start() -> datetime:
    return datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def is_active(sub: Subscription | None) -> bool:
    """Действует ли подписка: статус active и срок (если задан) не истёк. Иначе — тариф по умолчанию."""
    return bool(sub and sub.status == "active"
                and (sub.current_period_end is None or sub.current_period_end > datetime.now(UTC)))


def effective_limits(sub: Subscription) -> dict:
    return {**sub.plan.limits, **(sub.limits_override or {})}


async def plan_limits(session: AsyncSession, org_id: int) -> tuple[str, dict]:
    stmt = select(Subscription).where(Subscription.organization_id == org_id)
    sub = (await session.execute(stmt)).scalar_one_or_none()
    if is_active(sub):
        return sub.plan_code, effective_limits(sub)
    return DEFAULT_PLAN, PLANS[DEFAULT_PLAN]["limits"]


async def month_usage(session: AsyncSession, org_id: int) -> dict[str, float]:
    rows = await session.execute(
        select(UsageEvent.metric, func.coalesce(func.sum(UsageEvent.quantity), 0))
        .where(UsageEvent.organization_id == org_id, UsageEvent.at >= month_start())
        .group_by(UsageEvent.metric))
    return {metric: float(total) for metric, total in rows}


async def check(session: AsyncSession, org_id: int, metric: str, amount: float = 1,
                current: float | None = None) -> None:
    """Бросает QuotaExceeded, если операция превысит лимит.

    Для *_month-метрик использованное берётся из usage_events (без суффикса), для «штучных» (competitors, sources)
    текущее количество передаёт вызывающий в `current`."""
    _, limits = await plan_limits(session, org_id)
    limit = limits.get(metric)
    if limit is None:
        return
    if current is None:
        used = (await month_usage(session, org_id)).get(metric.removesuffix("_month"), 0.0)
    else:
        used = current
    # Для денег лимит — потолок: пока не достигнут, вызов разрешён (стоимость заранее неизвестна)
    if (used >= limit) if amount == 0 else (used + amount > limit):
        raise QuotaExceeded(metric, used, limit)
