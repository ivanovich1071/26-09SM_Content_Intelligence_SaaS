"""Публичный аудит без регистрации (лид-магнит).

Работает от служебной организации `_public-audits`: так расходы LLM на лид-магнит видны в usage_events и не
смешиваются с клиентскими. Защита бюджета: 1 аудит на IP и на email в сутки, общий суточный потолок, капча
(Cloudflare Turnstile, если задан TURNSTILE_SECRET). Результат отдаётся частично — полный после регистрации."""
import hashlib
import secrets
from datetime import timedelta

import httpx
from fastapi import HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import utcnow
from app.models import ContentAudit, Organization, Subscription

PUBLIC_SLUG = "_public-audits"  # «_» не бывает в slug пользовательских организаций
OPEN_CRITERIA = 2   # столько критериев (самых слабых) раскрыто полностью
OPEN_PROBLEMS = 3
TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def organization(session: AsyncSession) -> Organization:
    org = (await session.execute(select(Organization).where(Organization.slug == PUBLIC_SLUG))).scalar_one_or_none()
    if org is not None:
        return org
    org = Organization(name="Публичные аудиты", slug=PUBLIC_SLUG)
    session.add(org)
    try:
        await session.flush()
        # без лимитов тарифа: бюджет ограничивают лимиты по IP и суточный потолок
        session.add(Subscription(organization_id=org.id, plan_code="enterprise"))
        await session.commit()
    except IntegrityError:  # параллельный первый запрос уже создал
        await session.rollback()
        org = (await session.execute(select(Organization).where(Organization.slug == PUBLIC_SLUG))).scalar_one()
    return org


def client_ip(request: Request) -> str:
    if settings.trust_proxy_headers and (fwd := request.headers.get("x-forwarded-for")):
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def ip_hash(ip: str) -> str:
    return hashlib.sha256(f"{settings.secret_key}:{ip}".encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(24)


async def verify_captcha(token: str | None, ip: str) -> None:
    if not settings.turnstile_secret:
        return
    if not token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Подтвердите, что вы не робот")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.post(TURNSTILE_URL, data={"secret": settings.turnstile_secret, "response": token,
                                                     "remoteip": ip})
        ok = resp.status_code == 200 and resp.json().get("success") is True
    except (httpx.HTTPError, ValueError):
        ok = False
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Проверка «я не робот» не пройдена — попробуйте ещё раз")


async def check_limits(session: AsyncSession, org_id: int, ip_h: str, email: str | None) -> None:
    since = utcnow() - timedelta(days=1)
    base = select(func.count()).select_from(ContentAudit).where(
        ContentAudit.organization_id == org_id, ContentAudit.is_public.is_(True), ContentAudit.created_at >= since)
    total = (await session.execute(base)).scalar_one()
    if total >= settings.public_audits_per_day:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Лимит бесплатных аудитов на сегодня исчерпан. Зарегистрируйтесь — в аккаунте аудит "
                            "доступен по тарифу.")
    who = [ContentAudit.ip_hash == ip_h] + ([ContentAudit.email == email] if email else [])
    mine = (await session.execute(base.where(or_(*who)))).scalar_one()
    if mine >= settings.public_audits_per_ip_day:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Бесплатный аудит — один в сутки. Зарегистрируйтесь, чтобы проводить аудиты чаще "
                            "и сравнивать себя с конкурентами.")


def redact(data: dict) -> dict:
    """Публичная версия отчёта: балл и оценки всех критериев видны, полностью раскрыты только самые слабые,
    benchmark, пробелы и лучшие посты — после регистрации."""
    items = data["items"]
    scored = sorted((i for i in items if i["score"] is not None), key=lambda i: i["score"])
    opened = {i["criterion"] for i in scored[:OPEN_CRITERIA]}
    data["items"] = [i if i["criterion"] in opened else
                     {**i, "explanation": "", "evidence": [], "recommendations": [], "locked": True} for i in items]
    result = dict(data.get("result") or {})
    problems = result.get("problems") or []
    result["problems"], result["problems_hidden"] = problems[:OPEN_PROBLEMS], max(0, len(problems) - OPEN_PROBLEMS)
    result["gaps_hidden"], result["gaps"] = len(result.get("gaps") or []), []
    result["top_posts"], result["strengths"] = [], (result.get("strengths") or [])[:1]
    bm = result.get("benchmark") or {}
    result["benchmark"] = {k: bm.get(k) for k in ("enough", "message", "posts", "min_posts")}
    data["result"], data["locked"] = result, True
    return data
