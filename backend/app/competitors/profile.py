"""Competitor Analyst: статистика (код) + примеры постов → профиль конкурента (модель, задача analyze)."""
import json

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import prompts
from app.ai.router import AIRouter, model_for
from app.analysis import taxonomy
from app.competitors import stats
from app.core.db import SessionLocal, utcnow
from app.jobs.service import create_job, enqueue, set_status
from app.models import Competitor, Job, JobStatus, Source

MIN_POSTS = 5
SAMPLE_TOP, SAMPLE_RECENT = 8, 7
ACTIVE = (JobStatus.queued, JobStatus.running, JobStatus.analyzing)


class NotEnoughData(Exception):
    user_facing = True


class CompetitorProfile(BaseModel):
    summary: str
    positioning: str = ""
    audience: str = ""
    main_topics: list[str] = []
    formats: list[str] = []
    tone_of_voice: str = ""
    posting_frequency: str = ""
    ctas: list[str] = []
    content_patterns: list[str] = []
    strengths: list[str] = []
    weaknesses: list[str] = []
    opportunities: list[str] = []


async def source_ids(session: AsyncSession, competitor: Competitor) -> list[int]:
    return list((await session.execute(
        select(Source.id).where(Source.competitor_id == competitor.id, Source.enabled.is_(True)))).scalars())


def _sample(rows, top_ids: list[int]) -> list[dict]:
    by_id = {r.GlobalPost.id: r for r in rows}
    chosen = [by_id[i] for i in top_ids if i in by_id][:SAMPLE_TOP]
    for r in rows:  # rows отсортированы от новых к старым
        if len(chosen) >= SAMPLE_TOP + SAMPLE_RECENT:
            break
        if r not in chosen and (r.GlobalPost.text or r.GlobalPost.title):
            chosen.append(r)
    out = []
    for r in chosen:
        p, a = r.GlobalPost, r.PostAnalysis
        out.append({
            "площадка": r.GlobalSource.kind.value, "дата": p.published_at.date().isoformat(), "формат": p.media_type,
            "текст": "\n".join(filter(None, [p.title, (p.text or "")[:600]])),
            "overperformance": p.overperformance, "er": p.er,
            "разметка": {k: getattr(a, k) for k in ("topic", "content_type", "funnel_stage", "hook_type", "cta_type")}
            if a and not a.error else None,
        })
    return out


async def build(session: AsyncSession, competitor: Competitor, router: AIRouter, job_id: int | None = None) -> dict:
    ids = await source_ids(session, competitor)
    st = await stats.compute(session, competitor.organization_id, ids)
    if st["posts"] < MIN_POSTS:
        raise NotEnoughData(f"Недостаточно данных: {st['posts']} публикаций за {st['days']} дней, "
                            f"нужно от {MIN_POSTS}. Добавьте источники конкурента или дождитесь сбора.")
    rows = await stats.rows_for(session, competitor.organization_id, ids, st["days"])
    sources = (await session.execute(select(Source).where(Source.id.in_(ids)))).scalars().all()
    tax = await taxonomy.get(session, competitor.organization_id)
    user = json.dumps({
        "конкурент": competitor.name, "сайт": competitor.website, "заметки": competitor.notes,
        "источники": [{"площадка": s.global_source.kind.value, "название": s.global_source.title,
                       "описание": (s.global_source.description or "")[:300], "подписчики": s.global_source.followers}
                      for s in sources],
        "статистика": {k: v for k, v in st.items() if k not in ("weekly", "top_post_ids")},
        "примеры_публикаций": _sample(rows, st["top_post_ids"]),
    }, ensure_ascii=False, default=str)
    ai = await router.run("analyze", prompts.load("competitor/system").replace("{niche}", tax.niche), user,
                          org_id=competitor.organization_id, operation="competitor_profile",
                          schema=CompetitorProfile, job_id=job_id, max_tokens=2500)
    competitor.profile = {"stats": {k: v for k, v in st.items() if k != "top_post_ids"}, "ai": ai}
    competitor.profile_at = utcnow()
    competitor.profile_model = model_for("analyze")
    await session.commit()
    return {"posts": st["posts"], "analyzed": st["analyzed"]}


async def handle_profile_competitor(session: AsyncSession, job: Job, *, router: AIRouter | None = None) -> dict:
    competitor = await session.get(Competitor, job.params.get("competitor_id"))
    if competitor is None or competitor.organization_id != job.organization_id:
        raise NotEnoughData("Конкурент удалён")
    await set_status(session, job, JobStatus.analyzing, progress=20)
    return await build(session, competitor, router or AIRouter(session), job.id)


async def active_job(session: AsyncSession, org_id: int, competitor_id: int) -> Job | None:
    return (await session.execute(
        select(Job).where(Job.organization_id == org_id, Job.kind == "profile_competitor", Job.status.in_(ACTIVE),
                          Job.params["competitor_id"].as_integer() == competitor_id).limit(1))).scalar_one_or_none()


async def start(session: AsyncSession, pool, org_id: int, competitor_id: int, user_id: int | None = None) -> Job | None:
    if await active_job(session, org_id, competitor_id):
        return None
    job = await create_job(session, org_id, "profile_competitor", {"competitor_id": competitor_id}, user_id=user_id)
    await enqueue(pool, job)
    return job


async def after_analysis(analyze_job_id: int, pool) -> None:
    """Первый профиль строится сам, когда у конкурента накопились размеченные посты. Обновление — по кнопке."""
    async with SessionLocal() as session:
        job = await session.get(Job, analyze_job_id)
        if job is None or job.status != JobStatus.completed or not (job.result or {}).get("classified"):
            return
        source = await session.get(Source, job.params.get("source_id"))
        if source is None or source.competitor_id is None:
            return
        competitor = await session.get(Competitor, source.competitor_id)
        if competitor.profile is not None:
            return
        st = await stats.compute(session, competitor.organization_id, await source_ids(session, competitor))
        if st["analyzed"] >= MIN_POSTS:
            await start(session, pool, competitor.organization_id, competitor.id)
