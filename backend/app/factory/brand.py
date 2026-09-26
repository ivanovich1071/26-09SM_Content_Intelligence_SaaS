"""Профиль и голос бренда. Модель предлагает черновик по сайту, своим постам и последнему аудиту — сохраняет
пользователь (подсказка ничего не пишет в БД)."""
import json

from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import prompts
from app.ai.router import AIRouter
from app.analysis import dedupe, taxonomy
from app.audits.collect import site_snapshot
from app.connectors.base import InvalidSource, normalize_web_url
from app.models import BrandProfile, ContentAudit, GlobalPost, Organization, Source, SourceRole
from app.sources import sync

FIELDS = ("company", "website", "description", "offer", "audience", "differentiators", "proof_points", "cta", "tone",
          "do", "dont", "examples")
LISTS = ("differentiators", "proof_points", "do", "dont", "examples")


class BrandDraft(BaseModel):
    description: str = ""
    offer: str = ""
    audience: str = ""
    differentiators: list[str] = []
    proof_points: list[str] = []
    cta: str = ""
    tone: str = ""
    do: list[str] = []
    dont: list[str] = []


async def get(session: AsyncSession, org_id: int) -> BrandProfile | None:
    return (await session.execute(select(BrandProfile).where(BrandProfile.organization_id == org_id)
                                  .execution_options(populate_existing=True))).scalar_one_or_none()


def as_dict(b: BrandProfile | None) -> dict:
    if b is None:
        return {k: [] if k in LISTS else None for k in FIELDS}
    return {k: getattr(b, k) or ([] if k in LISTS else None) for k in FIELDS}


def for_prompt(b: BrandProfile | None) -> dict:
    """Только заполненное — пустые поля модели не нужны."""
    return {k: v for k, v in as_dict(b).items() if v and k != "examples"}


async def save(session: AsyncSession, org_id: int, data: dict, source: str = "manual") -> BrandProfile:
    b = await get(session, org_id)
    if b is None:
        b = BrandProfile(organization_id=org_id)
        session.add(b)
    for k in FIELDS:
        v = data.get(k)
        if k in LISTS:
            v = [" ".join(str(x).split())[:2000] if k != "examples" else str(x).strip()[:5000]
                 for x in (v or []) if str(x).strip()][:20]
        elif isinstance(v, str):
            v = v.strip()[:5000] or None
        setattr(b, k, v)
    b.source = source
    await session.commit()
    return b


async def suggest(session: AsyncSession, org_id: int, website: str | None, router: AIRouter) -> dict:
    org = await session.get(Organization, org_id)
    current = await get(session, org_id)
    site = None
    url = website or (current.website if current else None)
    if url:
        try:
            url = normalize_web_url(url)
        except InvalidSource:
            url = None
    if url:
        async with sync.make_fetcher() as http:
            site = await site_snapshot(http, url)
    own = (await session.execute(
        select(GlobalPost).join(Source, and_(Source.global_source_id == GlobalPost.global_source_id,
                                             Source.organization_id == org_id, Source.role == SourceRole.own))
        .where(dedupe.not_hidden(org_id)).order_by(GlobalPost.published_at.desc().nulls_last()).limit(12)
    )).scalars().all()
    audit = (await session.execute(select(ContentAudit).where(
        ContentAudit.organization_id == org_id, ContentAudit.stage == "done").order_by(ContentAudit.id.desc())
        .limit(1))).scalar_one_or_none()
    if not (site and site["pages"]) and not own:
        raise ValueError("Нет данных: укажите сайт компании или добавьте свои каналы с ролью «свой»")
    tax = await taxonomy.get(session, org_id)
    user = json.dumps({
        "компания": org.name, "ниша": tax.niche, "роли_аудитории": tax.roles,
        "сайт": site and {k: site.get(k) for k in ("url", "title", "description", "pages", "error")},
        "свои_публикации": [(p.text or p.title or "")[:600] for p in own],
        "аудит": {"резюме": audit.result.get("summary"),
                  "проблемы": [x["title"] for x in audit.result.get("problems", [])]}
        if audit and audit.result else None,
    }, ensure_ascii=False, default=str)
    data = await router.run("analyze", prompts.load("brand/system"), user, org_id=org_id, operation="brand_suggest",
                            schema=BrandDraft, max_tokens=2000)
    return {**data, "company": org.name if not current or not current.company else current.company,
            "website": url, "examples": [(p.text or "")[:1500] for p in own[:2] if (p.text or "").strip()]}
