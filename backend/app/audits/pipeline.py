"""Задача run_audit (Content Auditor). Перенос пайплайна VM_SM `app/audit/content_audit.py`:

источники → сбор → разметка → метрики и benchmark (код) → оценка 6 критериев (модель) → общий балл (код).
Без модели аудит не падает: оценки «по цифрам» для 4 критериев, остальные — «недостаточно данных»."""
import dataclasses
import json
from collections import defaultdict
from itertools import zip_longest

from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import prompts
from app.ai.openrouter import AIError
from app.ai.router import AIRouter, model_for
from app.analysis import classify, taxonomy
from app.analysis.pipeline import upsert_label
from app.audits import benchmark, collect
from app.audits.criteria import BY_KEY, CRITERIA, clamp, heuristics, overall
from app.billing.quotas import QuotaExceeded
from app.core.config import settings
from app.core.db import utcnow
from app.jobs.service import create_job, enqueue, set_status
from app.models import AuditItem, ContentAudit, Job, JobStatus
from app.sources import sync

STAGES = ("queued", "sources", "collect", "classify", "metrics", "audit", "done")
NO_KEY = "OPENROUTER_API_KEY не задан — оценки посчитаны по метрикам, без разбора текстов моделью."


class AuditError(Exception):
    user_facing = True


class CriterionAnswer(BaseModel):
    score: float | None = None
    explanation: str = ""
    evidence: list[dict] = []
    recommendations: list[str] = []


class AuditAnswer(BaseModel):
    criteria: dict[str, CriterionAnswer] = {}
    summary: str = ""
    strengths: list[str] = []
    problems: list[dict] = []


async def _stage(session: AsyncSession, audit: ContentAudit, job: Job, stage: str, progress: int) -> None:
    audit.stage = stage
    status = JobStatus.collecting if stage in ("sources", "collect") else JobStatus.analyzing
    await set_status(session, job, status, progress=progress)


def _error(msg: QuotaExceeded | AIError) -> str:
    return msg.detail["message"] if isinstance(msg, QuotaExceeded) else str(msg)[:300]


async def label_posts(session: AsyncSession, tax: taxonomy.OrgTaxonomy, org_id: int, rows: list[benchmark.Row],
                      router: AIRouter, job_id: int) -> str | None:
    """Размечает последние посты, по очереди из каждого канала — чтобы один частый канал не занял весь лимит.
    → текст замечания, если разметка остановилась."""
    pending = [r for r in rows if r.PostAnalysis is None or r.PostAnalysis.taxonomy_version < tax.version
               or (r.PostAnalysis.error and r.PostAnalysis.error != classify.NO_TEXT)]
    by_channel: dict[int, list] = defaultdict(list)
    for r in pending:
        by_channel[r.GlobalSource.id].append(r)
    mixed = [r for group in zip_longest(*by_channel.values()) for r in group if r is not None]
    todo = mixed[:settings.audit_max_posts]
    for r in todo:
        if classify.needs_text(r.GlobalPost):
            await upsert_label(session, org_id, r.GlobalPost.id, tax.version, {"error": classify.NO_TEXT})
    todo = [r for r in todo if not classify.needs_text(r.GlobalPost)]
    for i in range(0, len(todo), classify.BATCH):
        batch = [(r.GlobalPost, r.GlobalSource) for r in todo[i:i + classify.BATCH]]
        try:
            labels = await classify.classify_batch(router, tax, batch, org_id=org_id, job_id=job_id)
        except (AIError, QuotaExceeded) as e:
            await session.commit()
            return f"Разметка постов остановлена: {_error(e)}"
        for post, _ in batch:
            if post.id in labels:
                await upsert_label(session, org_id, post.id, tax.version, labels[post.id])
        await session.commit()
    await session.commit()
    return None


def _post_ref(r: benchmark.Row) -> dict:
    p = r.GlobalPost
    return {"post_id": p.id, "url": p.url, "date": p.published_at.date().isoformat() if p.published_at else None,
            "source": r.GlobalSource.title or r.GlobalSource.key}


def _sample_for_model(rows: list[benchmark.Row]) -> list[dict]:
    out = []
    for r in rows:
        p, a = r.GlobalPost, benchmark.label(r)
        out.append({"id": p.id, "площадка": r.GlobalSource.kind.value,
                    "дата": p.published_at.date().isoformat() if p.published_at else None,
                    "формат": p.media_type, "текст": "\n".join(filter(None, [p.title, (p.text or "")[:500]])),
                    "er": p.er, "overperformance": p.overperformance,
                    "разметка": {k: getattr(a, k) for k in ("topic", "target_role", "content_type", "funnel_stage",
                                                           "hook_type", "cta_type", "proof_type", "tone")}
                    if a else None})
    return out


def build_items(answer: dict | None, heur: dict, sample: list[benchmark.Row]) -> list[AuditItem]:
    refs = {r.GlobalPost.id: r for r in sample}
    items = []
    for n, c in enumerate(CRITERIA):
        ai = (answer or {}).get("criteria", {}).get(c.key)
        if ai is None:
            h = heur[c.key]
            items.append(AuditItem(criterion=c.key, sort=n, weight=c.weight, score=h["score"],
                                   explanation=h["explanation"], evidence=[], recommendations=[], ai=False))
            continue
        evidence = []
        for ev in ai["evidence"][:3]:
            fact = str(ev.get("fact") or "").strip()[:500]
            if not fact:
                continue
            try:
                pid = int(ev.get("post_id"))
            except (TypeError, ValueError):
                pid = None
            evidence.append({"fact": fact, **(_post_ref(refs[pid]) if pid in refs else {})})
        items.append(AuditItem(criterion=c.key, sort=n, weight=c.weight, score=clamp(ai["score"]),
                               explanation=ai["explanation"][:2000], evidence=evidence,
                               recommendations=[str(x)[:500] for x in ai["recommendations"][:3] if str(x).strip()],
                               ai=True))
    return items


def _problems(answer: dict | None, items: list[AuditItem]) -> list[dict]:
    if answer:
        return [{"title": str(p["title"])[:200], "detail": str(p.get("detail") or "")[:1000],
                 "criterion": p.get("criterion") if p.get("criterion") in BY_KEY else None}
                for p in answer["problems"] if isinstance(p, dict) and p.get("title")][:6]
    weak = sorted((i for i in items if i.score is not None and i.score < 5), key=lambda i: i.score)
    return [{"title": f"Слабое место: {BY_KEY[i.criterion].name.lower()}", "detail": i.explanation,
             "criterion": i.criterion} for i in weak]


def _channel_out(ch: collect.Channel, rows: list[benchmark.Row], days: int) -> dict:
    st = benchmark.channel_stats([r for r in rows if r.GlobalSource.id == ch.gs.id], days)
    return {"kind": ch.gs.kind.value, "url": ch.gs.url, "title": ch.gs.title, "followers": ch.gs.followers,
            "origin": ch.origin, "error": ch.error,
            "message": None if ch.sync.get("skipped") else ch.sync.get("message"),  # «данные свежие» — не новость
            **{k: st[k] for k in ("posts", "analyzed", "posts_per_week", "median_views", "median_er",
                                  "days_since_last_post")}}


async def run(session: AsyncSession, audit: ContentAudit, job: Job, router: AIRouter | None) -> dict:
    org_id, days, notes = audit.organization_id, settings.audit_days, []
    tax = await taxonomy.get(session, org_id)
    if audit.is_public:  # у публичной «организации» нет своей ниши
        tax = dataclasses.replace(tax, niche=f"компания «{audit.company}»; нишу определи по сайту и публикациям")

    await _stage(session, audit, job, "sources", 5)
    async with sync.make_fetcher() as http:
        site = await collect.site_snapshot(http, audit.website) if audit.website else None
        chans = await collect.channels(session, audit, site)
        await _stage(session, audit, job, "collect", 15)
        for i, ch in enumerate(chans):
            await collect.sync_channel(session, http, ch)
            await set_status(session, job, JobStatus.collecting, progress=15 + int(25 * (i + 1) / len(chans)))
    gs_ids = [c.gs.id for c in chans]
    rows = await benchmark.own_rows(session, org_id, gs_ids, days)
    if not rows and not (site and site["pages"]):
        reasons = [f"{c.gs.url}: {c.error}" for c in chans if c.error] + ([site["error"]] if site and site["error"]
                                                                         else [])
        raise AuditError("Не удалось собрать данные для аудита" + (": " + "; ".join(reasons) if reasons else
                         f" — у каналов нет публикаций за {days} дней."))

    await _stage(session, audit, job, "classify", 40)
    if router is None:
        notes.append(NO_KEY)
    elif note := await label_posts(session, tax, org_id, rows, router, job.id):
        notes.append(note)
        router = None if "OPENROUTER_API_KEY" in note or "Лимит" in note else router
    rows = await benchmark.own_rows(session, org_id, gs_ids, days)

    await _stage(session, audit, job, "metrics", 65)
    own = benchmark.channel_stats(rows, days)
    mk = await benchmark.market(session, org_id, gs_ids, days)
    table, gaps = benchmark.comparison(own, mk), benchmark.gaps(rows, mk)
    heur = heuristics(own, site)
    sample = benchmark.sample(rows, own["top_post_ids"])
    market_out = {k: v for k, v in mk.items() if k != "topics"}

    await _stage(session, audit, job, "audit", 75)
    answer = None
    if router is not None:
        user = json.dumps({
            "компания": audit.company,
            "сайт": site and {k: site[k] for k in ("url", "title", "description", "pages", "social_links", "forms",
                                                   "contacts", "error") if k in site},
            "каналы": [_channel_out(c, rows, days) for c in chans],
            "статистика_за_период": {k: v for k, v in own.items() if k != "top_post_ids"},
            "сравнение_с_рынком": table or market_out.get("message"),
            "темы_где_рынок_пишет_а_компания_нет": gaps,
            "примеры_публикаций": _sample_for_model(sample),
            "ориентиры_по_цифрам": heur,
        }, ensure_ascii=False, default=str)
        crit = "\n".join(f'- "{c.key}": {c.name} — {c.what}' for c in CRITERIA)
        system = prompts.load("audit/system").replace("{niche}", tax.niche).replace("{criteria}", crit)
        try:
            answer = await router.run("analyze", system, user, org_id=org_id, operation="content_audit",
                                      schema=AuditAnswer, job_id=job.id, max_tokens=5000)
        except (AIError, QuotaExceeded) as e:
            notes.append(f"Модель недоступна ({_error(e)}) — оценки посчитаны по метрикам.")

    items = build_items(answer, heur, sample)
    await session.execute(delete(AuditItem).where(AuditItem.audit_id == audit.id))
    for item in items:
        item.audit_id = audit.id
        session.add(item)
    audit.score = overall({i.criterion: i.score for i in items})
    audit.model = model_for("analyze") if answer else None
    by_id = {r.GlobalPost.id: r for r in rows}
    audit.result = {
        "days": days, "ai": answer is not None, "notes": notes,
        "summary": answer["summary"] if answer else None,
        "strengths": [str(s)[:300] for s in answer["strengths"][:5]] if answer else [],
        "problems": _problems(answer, items),
        "site": site and {k: v for k, v in site.items() if k != "pages"} | {
            "pages": [{k: p[k] for k in ("url", "kind", "title")} for p in site["pages"]]},
        "channels": [_channel_out(c, rows, days) for c in chans],
        "own": {k: v for k, v in own.items() if k != "top_post_ids"},
        "benchmark": {**market_out, "rows": table},
        "gaps": gaps,
        "top_posts": [{**_post_ref(by_id[i]), "text": (by_id[i].GlobalPost.text or by_id[i].GlobalPost.title
                                                        or "")[:300],
                       "er": by_id[i].GlobalPost.er, "overperformance": by_id[i].GlobalPost.overperformance}
                      for i in own["top_post_ids"][:5]],
    }
    audit.stage, audit.finished_at = "done", utcnow()
    await session.commit()
    return {"audit_id": audit.id, "score": audit.score, "posts": own["posts"], "analyzed": own["analyzed"],
            **({"message": " ".join(notes)} if notes else {})}


async def handle_run_audit(session: AsyncSession, job: Job, *, router: AIRouter | None = None) -> dict:
    audit = await session.get(ContentAudit, job.params.get("audit_id"))
    if audit is None or audit.organization_id != job.organization_id:
        raise AuditError("Аудит удалён")
    if router is None and settings.openrouter_api_key:
        router = AIRouter(session)
    return await run(session, audit, job, router)


async def start(session: AsyncSession, pool, audit: ContentAudit, user_id: int | None = None) -> Job:
    job = await create_job(session, audit.organization_id, "run_audit", {"audit_id": audit.id}, user_id=user_id)
    audit.job_id = job.id
    await session.commit()
    await enqueue(pool, job)
    return job
