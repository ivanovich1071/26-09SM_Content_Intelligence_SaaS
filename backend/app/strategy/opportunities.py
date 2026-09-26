"""Задача build_opportunities (Content Strategist): кандидаты кода + слабые места аудита → 10 тем.

Без модели темы собираются из лучших кандидатов по score с объяснением из цифр."""
import json

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import prompts
from app.ai.openrouter import AIError
from app.ai.router import AIRouter
from app.analysis import taxonomy
from app.audits.criteria import BY_KEY
from app.billing.quotas import QuotaExceeded
from app.core.config import settings
from app.core.db import SessionLocal
from app.jobs.service import create_job, enqueue, set_status
from app.models import AuditItem, ContentAudit, ContentOpportunity, Job, JobStatus
from app.strategy import candidates

COUNT = 10
FORMATS = ("telegram", "email", "linkedin", "vk", "article")
ACTIVE = (JobStatus.queued, JobStatus.running, JobStatus.analyzing)


class StrategyError(Exception):
    user_facing = True


class Opp(BaseModel):
    candidate: str | None = None
    title: str
    why: str = ""
    angle: str = ""
    formats: list[str] = []
    funnel_stage: str | None = None
    target_role: str | None = None
    fixes: list[str] = []


class StrategyAnswer(BaseModel):
    opportunities: list[Opp]


async def audit_context(session: AsyncSession, org_id: int, audit_id: int | None) -> tuple[ContentAudit | None, dict]:
    """Аудит, от которого строим темы: указанный или последний завершённый. → (аудит, выжимка для модели)."""
    stmt = select(ContentAudit).where(ContentAudit.organization_id == org_id, ContentAudit.stage == "done")
    stmt = stmt.where(ContentAudit.id == audit_id) if audit_id else stmt.order_by(ContentAudit.id.desc()).limit(1)
    audit = (await session.execute(stmt)).scalar_one_or_none()
    if audit is None:
        return None, {}
    items = (await session.execute(select(AuditItem).where(AuditItem.audit_id == audit.id))).scalars().all()
    weak = sorted((i for i in items if i.score is not None and i.score < 6), key=lambda i: i.score)
    return audit, {
        "компания": audit.company, "общий_балл": audit.score,
        "слабые_критерии": [{"ключ": i.criterion, "название": BY_KEY[i.criterion].name, "балл": i.score,
                             "пояснение": i.explanation[:300]} for i in weak],
        "проблемы": [p["title"] for p in (audit.result or {}).get("problems", [])][:6],
    }


def _why_from_numbers(m: dict) -> str:
    parts = [f"Рынок посвящает теме {m['share_market']:g}% публикаций, у вас — {m['share_own']:g}%"]
    if m["gap"] >= 5:
        parts[0] += f" (пробел {m['gap']:g} п.п.)"
    if m["trend_pp"] and m["trend_pp"] > 0:
        parts.append(f"доля темы у рынка выросла на {m['trend_pp']:g} п.п.")
    if m["median_er"] and m["market_median_er"] and m["median_er"] > m["market_median_er"]:
        parts.append(f"вовлечённость выше медианы рынка ({m['median_er']:g}% против {m['market_median_er']:g}%)")
    return "; ".join(parts) + "."


def _fallback(c: dict) -> dict:
    return {"candidate": c["id"], "title": c["topic"][:1].upper() + c["topic"][1:],
            "why": _why_from_numbers(c["market"]),
            "angle": "", "formats": ["telegram"], "funnel_stage": None, "target_role": None, "fixes": []}


def normalize(raw: list[dict], cands: dict[str, dict], tax: taxonomy.OrgTaxonomy, ai: bool) -> list[dict]:
    out, seen, per_cand = [], set(), {}
    funnel, roles = set(taxonomy.UNIVERSAL["funnel_stage"]), set(tax.roles)
    for o in raw:
        title = " ".join(str(o.get("title") or "").split())[:300]
        cand = cands.get(o.get("candidate") or "")
        if not title or title.lower() in seen or cand is None:  # тема без опоры на данные рынка не проходит
            continue
        if per_cand.get(cand["id"], 0) >= 2:
            continue
        seen.add(title.lower())
        per_cand[cand["id"]] = per_cand.get(cand["id"], 0) + 1
        stage = str(o.get("funnel_stage") or "").strip().lower().replace(" ", "_")
        role = str(o.get("target_role") or "").strip().lower()
        out.append({
            "title": title, "topic": cand["topic"], "why": str(o.get("why") or "")[:1500],
            "angle": str(o.get("angle") or "")[:1500],
            "formats": [f for f in dict.fromkeys(str(x).lower() for x in o.get("formats") or []) if f in FORMATS][:3]
            or ["telegram"],
            "funnel_stage": stage if stage in funnel else None, "target_role": role if role in roles else None,
            "fixes": [f for f in dict.fromkeys(o.get("fixes") or []) if f in BY_KEY],
            "market": cand["market"], "examples": cand["examples"], "score": cand["score"], "ai": ai,
        })
    return out


async def build(session: AsyncSession, job: Job, router: AIRouter | None) -> dict:
    org_id = job.organization_id
    data = await candidates.build(session, org_id, settings.strategy_days)
    if not data["enough"] or not data["candidates"]:
        raise StrategyError(
            f"Недостаточно данных: {data['market_posts']} размеченных публикаций рынка за {data['days']} дней, нужно "
            f"от {settings.strategy_min_market_posts}. Добавьте конкурентов и отраслевые каналы и дождитесь разметки.")
    await set_status(session, job, JobStatus.analyzing, progress=30)
    audit, audit_data = await audit_context(session, org_id, job.params.get("audit_id"))
    tax = await taxonomy.get(session, org_id)
    cands = {c["id"]: c for c in data["candidates"]}

    items: list[dict] = []
    note = None
    if router is not None:
        user = json.dumps({
            "кандидаты": [{"id": c["id"], "тема": c["topic"], "score": c["score"], "цифры": c["market"],
                           "под_темы": c["subtopics"], "форматы_рынка": c["formats"],
                           "примеры": [{"кто": e["source"], "текст": e["text"], "er": e["er"],
                                        "overperformance": e["overperformance"]} for e in c["examples"]]}
                          for c in data["candidates"]],
            "аудит": audit_data or None, "роли_аудитории": tax.roles,
            "стадии_воронки": taxonomy.UNIVERSAL["funnel_stage"],
        }, ensure_ascii=False, default=str)
        system = prompts.load("strategy/system").replace("{niche}", tax.niche).replace("{count}", str(COUNT))
        try:
            answer = await router.run("analyze", system, user, org_id=org_id, operation="content_opportunities",
                                      schema=StrategyAnswer, job_id=job.id, max_tokens=4000)
            items = normalize(answer["opportunities"], cands, tax, ai=True)
        except (AIError, QuotaExceeded) as e:
            note = "Модель недоступна — темы собраны по цифрам: " + (
                e.detail["message"] if isinstance(e, QuotaExceeded) else str(e)[:200])
    else:
        note = "OPENROUTER_API_KEY не задан — темы собраны по цифрам, без формулировок модели."
    used = {i["topic"] for i in items}
    for c in data["candidates"]:  # добираем до COUNT лучшими кандидатами, которых модель не взяла
        if len(items) >= COUNT:
            break
        if c["topic"] not in used and c["score"] > 0:  # без пробела, роста и отклика тему не навязываем
            items += normalize([_fallback(c)], cands, tax, ai=False)
    items = items[:COUNT]

    await set_status(session, job, JobStatus.analyzing, progress=80)
    await session.execute(update(ContentOpportunity).where(
        ContentOpportunity.organization_id == org_id, ContentOpportunity.status == "new").values(status="archived"))
    for rank, it in enumerate(items, 1):
        session.add(ContentOpportunity(organization_id=org_id, job_id=job.id, audit_id=audit.id if audit else None,
                                       rank=rank, **it))
    await session.commit()
    out = {"opportunities": len(items), "candidates": len(cands), "audit_id": audit.id if audit else None}
    if note:
        out["message"] = note
    return out


async def handle_build_opportunities(session: AsyncSession, job: Job, *, router: AIRouter | None = None) -> dict:
    await set_status(session, job, JobStatus.analyzing, progress=5)
    if router is None and settings.openrouter_api_key:
        router = AIRouter(session)
    return await build(session, job, router)


async def active_job(session: AsyncSession, org_id: int) -> Job | None:
    return (await session.execute(select(Job).where(
        Job.organization_id == org_id, Job.kind == "build_opportunities", Job.status.in_(ACTIVE)).limit(1)
    )).scalar_one_or_none()


async def start(session: AsyncSession, pool, org_id: int, audit_id: int | None = None,
                user_id: int | None = None) -> Job | None:
    if await active_job(session, org_id):
        return None
    job = await create_job(session, org_id, "build_opportunities", {"audit_id": audit_id}, user_id=user_id)
    await enqueue(pool, job)
    return job


async def after_audit(audit_job_id: int, pool) -> None:
    """После аудита организации — темы по нему, если данных рынка хватает (иначе задача упала бы с «мало данных»)."""
    async with SessionLocal() as session:
        job = await session.get(Job, audit_job_id)
        if job is None or job.status != JobStatus.completed:
            return
        audit = await session.get(ContentAudit, job.params.get("audit_id"))
        if audit is None or audit.is_public or audit.organization_id != job.organization_id:
            return
        data = await candidates.build(session, audit.organization_id, settings.strategy_days)
        if data["enough"] and data["candidates"]:
            await start(session, pool, audit.organization_id, audit.id)

