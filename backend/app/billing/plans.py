"""Тарифы. Лимиты предварительные — пересматриваются после замера себестоимости по usage_events.

Метрики с суффиксом _month считаются по usage_events за календарный месяц; остальные — по количеству объектов.
None — без ограничения."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Plan

# Тестовый режим: биллинга и оплат ещё нет — каждая компания по умолчанию на «Стартере».
# При вводе биллинга вернуть "free" и продавать starter через подписки.
DEFAULT_PLAN = "starter"

PLANS: dict[str, dict] = {
    "free": {"name": "Free", "price": 0, "limits": {
        "members": 1, "competitors": 0, "sources": 1, "websites": 1, "website_pages": 10,
        "audits_month": 1, "generations_month": 3, "ai_cost_usd_month": 0.5}},
    "starter": {"name": "Starter", "price": None, "limits": {
        "members": 2, "competitors": 3, "sources": 5, "websites": 1, "website_pages": 30,
        "audits_month": 10, "generations_month": 50, "ai_cost_usd_month": 5}},
    "professional": {"name": "Professional", "price": None, "limits": {
        "members": 5, "competitors": 10, "sources": 25, "websites": 5, "website_pages": 100,
        "audits_month": 50, "generations_month": 300, "ai_cost_usd_month": 25}},
    "agency": {"name": "Agency", "price": None, "limits": {
        "members": 15, "competitors": 50, "sources": 100, "websites": 20, "website_pages": 200,
        "audits_month": 200, "generations_month": 1500, "ai_cost_usd_month": 100}},
    "enterprise": {"name": "Enterprise", "price": None, "limits": {
        "members": None, "competitors": None, "sources": None, "websites": None, "website_pages": None,
        "audits_month": None, "generations_month": None, "ai_cost_usd_month": None}},
}


async def sync_plans(session: AsyncSession) -> None:
    """Код — источник истины для тарифов: при старте upsert в таблицу plans."""
    for sort, (code, spec) in enumerate(PLANS.items()):
        plan = await session.get(Plan, code)
        if plan is None:
            plan = Plan(code=code)
            session.add(plan)
        plan.name, plan.price_month_usd, plan.limits, plan.sort = spec["name"], spec["price"], spec["limits"], sort
    await session.commit()
