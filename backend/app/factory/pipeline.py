"""Задача generate_content: Writer (новый черновик) / Editor (правка по инструкции) / QA → новая версия.

Черновик пишется по RAG-контексту (context.py), правка — по тому же контексту предыдущей версии. После каждой
генерации — QA: проверки кода всегда, проверка моделью (задача qa) — если она доступна."""
import json

from pydantic import BaseModel, create_model
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import prompts
from app.ai.embeddings import EmbeddingProvider, get_embedding_provider
from app.ai.openrouter import AIError
from app.ai.router import AIRouter, model_for
from app.analysis import taxonomy
from app.billing import usage
from app.billing.quotas import QuotaExceeded
from app.core.config import settings
from app.core.db import utcnow
from app.factory import brand, context, qa
from app.factory.formats import FORMATS, field_keys
from app.jobs.service import create_job, enqueue, set_status
from app.models import ContentOpportunity, ContentProject, ContentVersion, Job, JobStatus

ACTIVE = (JobStatus.queued, JobStatus.running, JobStatus.analyzing)


class FactoryError(Exception):
    user_facing = True


class QACheck(BaseModel):
    code: str = "other"
    level: str = "ok"
    message: str = ""


class QAAnswer(BaseModel):
    checks: list[QACheck] = []
    summary: str = ""


def draft_schema(fmt: str) -> type[BaseModel]:
    return create_model(f"Draft_{fmt}", **{k: (str, ...) for k in field_keys(fmt)})


def _brand_text(b) -> str:
    return json.dumps(brand.for_prompt(b), ensure_ascii=False, indent=1) if b else "профиль бренда не заполнен"


def _system(name: str, fmt: str, b, niche: str) -> str:
    spec = FORMATS[fmt]
    fields = json.dumps({f.key: f.label for f in spec.fields}, ensure_ascii=False)
    return (prompts.load(name).replace("{niche}", niche).replace("{format_label}", spec.label)
            .replace("{format_rules}", spec.rules).replace("{brand}", _brand_text(b)).replace("{fields}", fields))


def _clean(fields: dict, fmt: str) -> dict:
    return {k: str(fields.get(k) or "").strip() for k in field_keys(fmt)}


def allowed_facts(project: ContentProject, b, ctx: dict) -> str:
    """Откуда разрешено брать цифры: пожелания, бренд, цифры «Стратегии»."""
    parts = [project.title, project.brief or "", json.dumps(brand.for_prompt(b), ensure_ascii=False) if b else "",
             json.dumps((ctx.get("opportunity") or {}).get("цифры") or {}, ensure_ascii=False)]
    return "\n".join(parts)


async def run_qa(session: AsyncSession, project: ContentProject, fields: dict, ctx: dict, router: AIRouter | None,
                 job_id: int | None) -> dict:
    b = await brand.get(session, project.organization_id)
    checks = qa.code_checks(project.format, fields, ctx, allowed_facts(project, b, ctx))
    ai = None
    if router is not None:
        tax = await taxonomy.get(session, project.organization_id)
        user = json.dumps({"материал": fields, "тема": project.title, "пожелания": project.brief,
                           "факты_бренда": (b.proof_points if b else []) or [],
                           "контекст_рынка": (ctx.get("opportunity") or {}).get("почему")}, ensure_ascii=False)
        try:
            data = await router.run("qa", _system("qa/system", project.format, b, tax.niche), user,
                                    org_id=project.organization_id, operation="content_qa", schema=QAAnswer,
                                    job_id=job_id, max_tokens=1500)
            ai = [{"code": c["code"][:30], "level": c["level"] if c["level"] in qa.LEVELS else "warn",
                   "message": c["message"][:500]} for c in data["checks"] if c["message"] or c["level"] != "ok"]
        except (AIError, QuotaExceeded):
            ai = None
    return {**qa.merge(checks, ai), "at": utcnow().isoformat()}


async def latest(session: AsyncSession, project_id: int) -> ContentVersion | None:
    return (await session.execute(select(ContentVersion).where(ContentVersion.project_id == project_id)
                                  .order_by(ContentVersion.number.desc()).limit(1))).scalar_one_or_none()


async def add_version(session: AsyncSession, project: ContentProject, kind: str, fields: dict, ctx: dict,
                      qa_result: dict, *, instruction: str | None = None, model: str | None = None,
                      user_id: int | None = None) -> ContentVersion:
    n = (await session.execute(select(func.coalesce(func.max(ContentVersion.number), 0)).where(
        ContentVersion.project_id == project.id))).scalar_one()
    v = ContentVersion(project_id=project.id, number=n + 1, kind=kind, instruction=instruction, fields=fields,
                       context=ctx, qa=qa_result, model=model, created_by=user_id)
    session.add(v)
    project.updated_at = utcnow()
    await session.commit()
    return v


async def handle(session: AsyncSession, job: Job, router: AIRouter | None, embedder: EmbeddingProvider | None) -> dict:
    project = await session.get(ContentProject, job.params.get("project_id"))
    if project is None or project.organization_id != job.organization_id:
        raise FactoryError("Материал удалён")
    action, fmt = job.params.get("action"), project.format
    await set_status(session, job, JobStatus.analyzing, progress=10)
    b = await brand.get(session, project.organization_id)
    tax = await taxonomy.get(session, project.organization_id)
    prev = await latest(session, project.id)

    if action == "qa":
        if prev is None:
            raise FactoryError("Нет версии для проверки")
        prev.qa = await run_qa(session, project, prev.fields, prev.context or {}, router, job.id)
        await session.commit()
        return {"version": prev.number, "qa": prev.qa["status"]}
    if router is None:
        raise FactoryError("OPENROUTER_API_KEY не задан — генерация недоступна. Текст можно написать вручную.")

    if action == "write":
        ctx = await context.build(session, project, embedder)
        await set_status(session, job, JobStatus.analyzing, progress=35)
        user = json.dumps({"тема": project.title, "пожелания": project.brief, "контекст": {
            "почему_тема": ctx["opportunity"], "лучшие_публикации_рынка": [
                {"кто": p["source"], "текст": p["text"], "overperformance": p["overperformance"]}
                for p in ctx["market_posts"]],
            "приёмы_темы": ctx["patterns"], "прошлые_посты_клиента": [p["text"] for p in ctx["own_posts"]],
            "аудит": ctx["audit"]},
            "образцы_голоса_бренда": [e[:1500] for e in (b.examples if b else [])[:2]]},
            ensure_ascii=False, default=str)
        system, operation, instruction = _system("writer/system", fmt, b, tax.niche), "content_write", None
    elif action == "edit":
        if prev is None:
            raise FactoryError("Нет версии для правки")
        ctx, instruction = prev.context or {}, job.params.get("instruction") or ""
        problems = [c["message"] for c in (prev.qa or {}).get("checks", []) if c["level"] != "ok"]
        user = json.dumps({"текущая_версия": prev.fields, "инструкция": instruction, "замечания_проверки": problems,
                           "тема": project.title}, ensure_ascii=False)
        system, operation = _system("editor/system", fmt, b, tax.niche), "content_edit"
    else:
        raise FactoryError(f"Неизвестное действие: {action}")

    fields = _clean(await router.run("write", system, user, org_id=project.organization_id, operation=operation,
                                     schema=draft_schema(fmt), job_id=job.id, max_tokens=6000 if fmt == "article"
                                     else 2500), fmt)
    await set_status(session, job, JobStatus.analyzing, progress=75)
    qa_result = await run_qa(session, project, fields, ctx, router, job.id)
    # в квоту генераций идут только удачные черновики и правки
    await usage.record(session, project.organization_id, "generations", operation, job_id=job.id, commit=False)
    v = await add_version(session, project, action, fields, ctx, qa_result, instruction=instruction,
                          model=model_for("write"), user_id=job.created_by)
    return {"version": v.number, "qa": qa_result["status"]}


async def handle_generate_content(session: AsyncSession, job: Job, *, router: AIRouter | None = None,
                                  embedder: EmbeddingProvider | None = None) -> dict:
    if settings.openrouter_api_key:
        router = router or AIRouter(session)
        embedder = embedder or get_embedding_provider()
    return await handle(session, job, router, embedder)


async def active_job(session: AsyncSession, org_id: int, project_id: int) -> Job | None:
    return (await session.execute(select(Job).where(
        Job.organization_id == org_id, Job.kind == "generate_content", Job.status.in_(ACTIVE),
        Job.params["project_id"].as_integer() == project_id).limit(1))).scalar_one_or_none()


async def start(session: AsyncSession, pool, project: ContentProject, action: str, user_id: int | None,
                instruction: str | None = None) -> Job | None:
    if await active_job(session, project.organization_id, project.id):
        return None
    job = await create_job(session, project.organization_id, "generate_content",
                           {"project_id": project.id, "action": action, "instruction": instruction}, user_id=user_id)
    await enqueue(pool, job)
    return job


async def mark_opportunity(session: AsyncSession, project: ContentProject, status: str) -> None:
    if project.opportunity_id:
        opp = await session.get(ContentOpportunity, project.opportunity_id)
        if opp is not None and opp.organization_id == project.organization_id:
            opp.status = status
