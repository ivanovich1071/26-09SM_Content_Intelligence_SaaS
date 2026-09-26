"""Задача analyze_source: метрики → точные дубли → разметка по таксономии организации → эмбеддинги → смысловые дубли.

Ставится автоматически после каждого sync (в т.ч. когда сбор пропущен как «свежий»: у организации B могут быть
неразмеченные посты канала, который собрала A). Без OPENROUTER_API_KEY метрики и дубли всё равно считаются."""
import logging
from datetime import timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EmbeddingError, EmbeddingProvider, get_embedding_provider
from app.ai.openrouter import AIError
from app.ai.router import AIRouter
from app.analysis import classify, dedupe, embed, metrics, taxonomy
from app.billing.quotas import QuotaExceeded
from app.core.config import settings
from app.core.db import SessionLocal, utcnow
from app.jobs.service import create_job, enqueue, set_status
from app.models import GlobalPost, GlobalSource, Job, JobStatus, PostAnalysis, Source

log = logging.getLogger("sm.analysis")
ACTIVE = (JobStatus.queued, JobStatus.running, JobStatus.analyzing)
NO_KEY = "OPENROUTER_API_KEY не задан — разметка и эмбеддинги пропущены, метрики посчитаны"


class AnalysisError(Exception):
    user_facing = True


async def _upsert(session: AsyncSession, org_id: int, post_id: int, version: int, values: dict) -> None:
    row = {"organization_id": org_id, "post_id": post_id, "taxonomy_version": version,
           "model": classify.classifier_model(), "analyzed_at": utcnow(), "error": None,
           **{c: None for c in (*taxonomy.UNIVERSAL, "topic", "target_role", *classify.BOOL_FIELDS, "summary")},
           **values}
    stmt = insert(PostAnalysis).values(row)
    await session.execute(stmt.on_conflict_do_update(
        constraint="uq_post_analysis_org_post", set_={k: stmt.excluded[k] for k in row if k not in (
            "organization_id", "post_id")}))


async def pending_for_org(session: AsyncSession, org_id: int, gs: GlobalSource,
                          version: int) -> list[tuple[GlobalPost, GlobalSource]]:
    """Не размеченные этой организацией, размеченные старой таксономией или с ошибкой модели. Свежие — первыми."""
    since = utcnow() - timedelta(days=settings.source_history_days)
    stmt = (select(GlobalPost, GlobalSource).join(GlobalSource)
            .outerjoin(PostAnalysis, and_(PostAnalysis.post_id == GlobalPost.id,
                                          PostAnalysis.organization_id == org_id))
            .where(GlobalPost.global_source_id == gs.id, dedupe.not_hidden(org_id),
                   or_(GlobalPost.published_at.is_(None), GlobalPost.published_at >= since),
                   or_(PostAnalysis.id.is_(None), PostAnalysis.taxonomy_version < version,
                       and_(PostAnalysis.error.is_not(None), PostAnalysis.error != classify.NO_TEXT)))
            .order_by(GlobalPost.published_at.desc().nulls_last())
            .limit(settings.classify_max_posts_per_job))
    return [tuple(r) for r in (await session.execute(stmt)).all()]


async def classify_source(session: AsyncSession, job: Job, gs: GlobalSource, router: AIRouter) -> dict:
    org_id = job.organization_id
    tax = await taxonomy.get(session, org_id)
    rows = await pending_for_org(session, org_id, gs, tax.version)
    no_text = [(p, s) for p, s in rows if classify.needs_text(p)]
    todo = [(p, s) for p, s in rows if not classify.needs_text(p)]
    for p, _ in no_text:
        await _upsert(session, org_id, p.id, tax.version, {"error": classify.NO_TEXT})
    await session.commit()

    ok = failed = 0
    stopped: str | None = None
    for i in range(0, len(todo), classify.BATCH):
        batch = todo[i:i + classify.BATCH]
        try:
            labels = await classify.classify_batch(router, tax, batch, org_id=org_id, job_id=job.id)
        except QuotaExceeded as e:
            stopped = e.detail["message"]
            break
        except AIError as e:
            if "OPENROUTER_API_KEY" in str(e):
                stopped = NO_KEY
                break
            labels, error = {}, f"ошибка модели: {str(e)[:300]}"
        else:
            error = "модель не вернула разметку для поста"
        for p, _ in batch:
            if p.id in labels:
                await _upsert(session, org_id, p.id, tax.version, labels[p.id])
                ok += 1
            else:
                await _upsert(session, org_id, p.id, tax.version, {"error": error})
                failed += 1
        await session.commit()
        await set_status(session, job, JobStatus.analyzing,
                         progress=20 + int(60 * min(i + classify.BATCH, len(todo)) / max(len(todo), 1)))
    out = {"classified": ok, "classify_errors": failed, "no_text": len(no_text), "taxonomy_version": tax.version}
    if stopped:
        out["classify_stopped"] = stopped
    return out


async def handle_analyze_source(session: AsyncSession, job: Job, *, router: AIRouter | None = None,
                                embedder: EmbeddingProvider | None = None) -> dict:
    source = await session.get(Source, job.params.get("source_id"))
    if source is None or source.organization_id != job.organization_id:
        raise AnalysisError("Источник удалён")
    gs = source.global_source
    await set_status(session, job, JobStatus.analyzing, progress=5)
    result: dict = {"metrics": await metrics.calculate(session, gs), "duplicates": await dedupe.exact(session, gs)}
    await set_status(session, job, JobStatus.analyzing, progress=20)

    if not settings.openrouter_api_key and router is None:
        result["message"] = NO_KEY
        return result
    result.update(await classify_source(session, job, gs, router or AIRouter(session)))

    try:
        embedded = await embed.embed_source(session, gs, embedder or get_embedding_provider(),
                                            org_id=job.organization_id, job_id=job.id)
        result["embedded"] = len(embedded)
        result["duplicates"] += await dedupe.semantic(session, gs, embedded)
    except (EmbeddingError, QuotaExceeded) as e:
        await session.rollback()
        msg = e.detail["message"] if isinstance(e, QuotaExceeded) else str(e)
        result["embed_error"] = f"Эмбеддинги не посчитаны: {msg}"
    return result


async def active_job(session: AsyncSession, org_id: int, source_id: int) -> Job | None:
    stmt = (select(Job).where(Job.organization_id == org_id, Job.kind == "analyze_source", Job.status.in_(ACTIVE),
                              Job.params["source_id"].as_integer() == source_id).limit(1))
    return (await session.execute(stmt)).scalar_one_or_none()


async def start(session: AsyncSession, pool, org_id: int, source_id: int, user_id: int | None = None) -> Job | None:
    """Ставит analyze_source, если такой задачи для источника ещё нет в работе."""
    if await active_job(session, org_id, source_id):
        return None
    job = await create_job(session, org_id, "analyze_source", {"source_id": source_id}, user_id=user_id)
    await enqueue(pool, job)
    return job


async def after_sync(sync_job_id: int, pool) -> None:
    async with SessionLocal() as session:
        job = await session.get(Job, sync_job_id)
        if job is None or job.status != JobStatus.completed:
            return
        source_id = job.params.get("source_id")
        if await session.get(Source, source_id) is None:
            return
        await start(session, pool, job.organization_id, source_id)
