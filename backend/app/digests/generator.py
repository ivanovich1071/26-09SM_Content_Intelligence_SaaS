"""Задача generate_digest (Digest Generator): цифры периода (stats.py) → тексты модели или шаблон → рассылка.

Расписание: cron schedule_digests каждый час ставит задачи организациям, у которых подошло next_run_at."""
import json
from calendar import monthrange
from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import prompts
from app.ai.openrouter import AIError
from app.ai.router import AIRouter, model_for
from app.analysis import taxonomy
from app.billing.quotas import QuotaExceeded
from app.core.config import settings
from app.core.db import utcnow
from app.digests import mailer, render, stats
from app.factory import brand
from app.jobs.service import create_job, enqueue, set_status
from app.models import Digest, DigestSchedule, Job, JobStatus, Organization

ACTIVE = (JobStatus.queued, JobStatus.running, JobStatus.analyzing)
PERIOD_DAYS = {"weekly": 7, "monthly": 30}


class DigestText(BaseModel):
    headline: str = ""
    summary: str = ""
    market: str = ""
    topics: str = ""
    competitors: str = ""
    top_posts: str = ""
    unusual: str = ""
    own: str = ""
    recommendations: list[dict] = []
    ideas: list[dict] = []


def title(org: Organization, d: Digest) -> str:
    kind = {7: "недельный", 30: "месячный"}.get(d.days, f"за {d.days} дн.")
    return f"Дайджест {kind}: {org.name}, {d.period_from:%d.%m}–{d.period_to:%d.%m.%Y}"


async def send_email(session: AsyncSession, d: Digest, to: list[str]) -> None:
    org = await session.get(Organization, d.organization_id)
    t = title(org, d)
    url = f"{settings.app_url.rstrip('/')}/digest/{d.id}"
    try:
        await mailer.send(to, t, render.markdown(t, d.stats, d.sections),
                          render.email_html(t, d.stats, d.sections, url))
    except mailer.MailError as e:
        d.email_error = str(e)
        await session.commit()
        raise
    d.emailed_at, d.email_error = utcnow(), None
    await session.commit()


async def build(session: AsyncSession, job: Job, router: AIRouter | None) -> dict:
    org_id, days = job.organization_id, int(job.params.get("days") or 7)
    until = utcnow()
    st = await stats.build(session, org_id, until, days)
    await set_status(session, job, JobStatus.analyzing, progress=50)
    sections, ai, note = render.fallback(st), False, None
    if not st["enough"]:
        note = "За период нет публикаций и изменений сайтов — дайджест собран по пустым данным."
    elif router is not None:
        tax = await taxonomy.get(session, org_id)
        b = await brand.get(session, org_id)
        system = (prompts.load("digest/system").replace("{niche}", tax.niche)
                  .replace("{brand}", json.dumps(brand.for_prompt(b), ensure_ascii=False) if b else "не заполнен"))
        try:
            data = await router.run("analyze", system, json.dumps(st, ensure_ascii=False, default=str), org_id=org_id,
                                    operation="digest", schema=DigestText, job_id=job.id, max_tokens=4000)
            sections, ai = render.clean(data, st), True
        except (AIError, QuotaExceeded) as e:
            note = "Модель недоступна — выводы собраны по цифрам: " + (
                e.detail["message"] if isinstance(e, QuotaExceeded) else str(e)[:200])
    else:
        note = "OPENROUTER_API_KEY не задан — выводы собраны по цифрам."
    d = Digest(organization_id=org_id, job_id=job.id, period_from=until - timedelta(days=days), period_to=until,
               days=days, trigger=job.params.get("trigger") or "manual", stats=st, sections=sections, ai=ai,
               model=model_for("analyze") if ai else None)
    session.add(d)
    await session.commit()
    out = {"digest_id": d.id, "ai": ai}
    to = job.params.get("recipients") or []
    if to:
        try:
            await send_email(session, d, to)
            out["emailed"] = len(to)
        except mailer.MailError as e:
            note = f"{note} {e}".strip() if note else str(e)
    if note:
        out["message"] = note
    return out


async def handle_generate_digest(session: AsyncSession, job: Job, *, router: AIRouter | None = None) -> dict:
    await set_status(session, job, JobStatus.analyzing, progress=10)
    if router is None and settings.openrouter_api_key:
        router = AIRouter(session)
    return await build(session, job, router)


async def active_job(session: AsyncSession, org_id: int) -> Job | None:
    return (await session.execute(select(Job).where(
        Job.organization_id == org_id, Job.kind == "generate_digest", Job.status.in_(ACTIVE)).limit(1)
    )).scalar_one_or_none()


async def start(session: AsyncSession, pool, org_id: int, days: int, *, trigger: str = "manual",
                recipients: list[str] | None = None, user_id: int | None = None) -> Job | None:
    if await active_job(session, org_id):
        return None
    job = await create_job(session, org_id, "generate_digest",
                           {"days": days, "trigger": trigger, "recipients": recipients or []}, user_id=user_id)
    await enqueue(pool, job)
    return job


# --- расписание ---

def period_days(s: DigestSchedule) -> int:
    return PERIOD_DAYS.get(s.period, s.every_days)


def next_run(s: DigestSchedule, after: datetime) -> datetime:
    """Ближайший запуск строго после `after` (UTC)."""
    at = after.replace(minute=0, second=0, microsecond=0, hour=s.hour)
    if s.period == "weekly":
        at += timedelta(days=(s.weekday - at.weekday()) % 7)
        return at if at > after else at + timedelta(days=7)
    if s.period == "monthly":
        at = at.replace(day=min(s.day, 28))
        if at > after:
            return at
        year, month = (at.year + 1, 1) if at.month == 12 else (at.year, at.month + 1)
        return at.replace(year=year, month=month, day=min(s.day, monthrange(year, month)[1]))
    base = (s.last_run_at or after).replace(minute=0, second=0, microsecond=0, hour=s.hour)
    at = base + timedelta(days=s.every_days) if s.last_run_at else base
    while at <= after:
        at += timedelta(days=s.every_days)
    return at


async def schedule_due(session: AsyncSession, pool) -> int:
    now = utcnow()
    due = (await session.execute(select(DigestSchedule).where(
        DigestSchedule.enabled.is_(True), DigestSchedule.next_run_at <= now))).scalars().all()
    created = 0
    for s in due:
        job = await start(session, pool, s.organization_id, period_days(s), trigger="schedule",
                          recipients=s.recipients if s.send_email else [])
        if job is None:
            continue  # прошлый дайджест ещё собирается — попробуем через час
        s.last_run_at = now
        s.next_run_at = next_run(s, now)
        created += 1
    await session.commit()
    return created
