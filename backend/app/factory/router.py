from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.openrouter import AIError
from app.ai.router import AIRouter
from app.billing import quotas
from app.core.config import settings
from app.core.db import get_session
from app.core.deps import Tenant, get_tenant, require_role
from app.factory import brand, export, pipeline, qa
from app.factory.formats import FORMATS, field_keys
from app.models import ContentOpportunity, ContentProject, ContentVersion, Job, Role
from app.sources.router import JobBrief

brand_router = APIRouter(prefix="/brand", tags=["brand"])
router = APIRouter(prefix="/factory", tags=["factory"])

FormatKey = Literal["telegram", "email", "linkedin", "vk", "article"]


class BrandIn(BaseModel):
    company: str | None = Field(default=None, max_length=200)
    website: str | None = Field(default=None, max_length=1000)
    description: str | None = None
    offer: str | None = None
    audience: str | None = None
    differentiators: list[str] = []
    proof_points: list[str] = []
    cta: str | None = None
    tone: str | None = None
    do: list[str] = []
    dont: list[str] = []
    examples: list[str] = []


class BrandOut(BrandIn):
    source: str | None = None
    updated_at: datetime | None = None


class SuggestIn(BaseModel):
    website: str | None = Field(default=None, max_length=1000)


class ProjectIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    brief: str | None = Field(default=None, max_length=5000)
    format: FormatKey
    opportunity_id: int | None = None
    generate: bool = True


class ProjectPatch(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=300)
    brief: str | None = Field(default=None, max_length=5000)
    status: Literal["draft", "approved", "published", "archived"] | None = None


class GenerateIn(BaseModel):
    action: Literal["write", "edit", "qa"]
    instruction: str | None = Field(default=None, max_length=2000)


class VersionIn(BaseModel):
    fields: dict[str, str]


class VersionOut(BaseModel):
    id: int
    number: int
    kind: str
    instruction: str | None
    fields: dict
    context: dict
    qa: dict
    model: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProjectBrief(BaseModel):
    id: int
    title: str
    format: str
    status: str
    opportunity_id: int | None
    versions: int
    qa: str | None
    job: JobBrief | None
    created_at: datetime
    updated_at: datetime


class ProjectOut(ProjectBrief):
    brief: str | None
    items: list[VersionOut]


async def _job(session: AsyncSession, p: ContentProject) -> Job | None:
    return (await session.execute(select(Job).where(
        Job.organization_id == p.organization_id, Job.kind == "generate_content",
        Job.params["project_id"].as_integer() == p.id).order_by(Job.id.desc()).limit(1))).scalar_one_or_none()


async def _out(session: AsyncSession, p: ContentProject, full: bool = True) -> dict:
    versions = (await session.execute(select(ContentVersion).where(ContentVersion.project_id == p.id)
                                      .order_by(ContentVersion.number.desc()))).scalars().all()
    job = await _job(session, p)
    base = dict(id=p.id, title=p.title, format=p.format, status=p.status, opportunity_id=p.opportunity_id,
                versions=len(versions), qa=versions[0].qa.get("status") if versions and versions[0].qa else None,
                job=JobBrief.model_validate(job) if job else None, created_at=p.created_at, updated_at=p.updated_at)
    return {**base, "brief": p.brief, "items": versions} if full else base


async def _start(request: Request, session: AsyncSession, tenant: Tenant, p: ContentProject, action: str,
                 instruction: str | None = None) -> Job:
    if action in ("write", "edit"):
        await quotas.check(session, tenant.org_id, "generations_month")
    job = await pipeline.start(session, getattr(request.app.state, "arq", None), p, action, tenant.user.id,
                               instruction)
    if job is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Материал уже генерируется")
    return job


# --- бренд ---

@brand_router.get("", response_model=BrandOut)
async def get_brand(tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    b = await brand.get(session, tenant.org_id)
    return {**brand.as_dict(b), "source": b.source if b else None, "updated_at": b.updated_at if b else None}


@brand_router.put("", response_model=BrandOut)
async def put_brand(body: BrandIn, tenant: Tenant = Depends(require_role(Role.member)),
                    session: AsyncSession = Depends(get_session)):
    b = await brand.save(session, tenant.org_id, body.model_dump())
    return {**brand.as_dict(b), "source": b.source, "updated_at": b.updated_at}


@brand_router.post("/suggest", response_model=BrandIn)
async def suggest_brand(body: SuggestIn, tenant: Tenant = Depends(require_role(Role.member)),
                        session: AsyncSession = Depends(get_session)):
    """Черновик профиля и голоса по сайту и своим постам. Ничего не сохраняет."""
    if not settings.openrouter_api_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "OPENROUTER_API_KEY не задан — подсказка недоступна")
    try:
        return await brand.suggest(session, tenant.org_id, body.website, AIRouter(session))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except AIError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Модель не ответила: {e}") from e


# --- Контент Завод ---

@router.get("/formats")
async def formats(_: Tenant = Depends(get_tenant)):
    return [{"key": f.key, "label": f.label, "rules": f.rules,
             "fields": [{"key": x.key, "label": x.label, "min": x.min, "max": x.max, "multiline": x.multiline}
                        for x in f.fields]} for f in FORMATS.values()]


@router.get("/projects", response_model=list[ProjectBrief])
async def list_projects(status_: Literal["active", "draft", "approved", "published", "archived"] = Query(
                            "active", alias="status"),
                        tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    stmt = tenant.scoped(select(ContentProject), ContentProject)
    stmt = stmt.where(ContentProject.status != "archived") if status_ == "active" else \
        stmt.where(ContentProject.status == status_)
    rows = (await session.execute(stmt.order_by(ContentProject.updated_at.desc()).limit(200))).scalars().all()
    return [await _out(session, p, full=False) for p in rows]


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectIn, request: Request, tenant: Tenant = Depends(require_role(Role.member)),
                         session: AsyncSession = Depends(get_session)):
    if body.opportunity_id is not None:
        await tenant.get(session, ContentOpportunity, body.opportunity_id)
    if body.generate:
        await quotas.check(session, tenant.org_id, "generations_month")
    p = ContentProject(organization_id=tenant.org_id, title=body.title.strip(), brief=body.brief, format=body.format,
                       opportunity_id=body.opportunity_id, created_by=tenant.user.id)
    session.add(p)
    await session.flush()
    await pipeline.mark_opportunity(session, p, "in_factory")
    await session.commit()
    if body.generate:
        await _start(request, session, tenant, p, "write")
    return await _out(session, p)


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: int, tenant: Tenant = Depends(get_tenant),
                      session: AsyncSession = Depends(get_session)):
    return await _out(session, await tenant.get(session, ContentProject, project_id))


@router.patch("/projects/{project_id}", response_model=ProjectOut)
async def patch_project(project_id: int, body: ProjectPatch, tenant: Tenant = Depends(require_role(Role.member)),
                        session: AsyncSession = Depends(get_session)):
    p = await tenant.get(session, ContentProject, project_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(p, k, v.strip() if isinstance(v, str) and k == "title" else v)
    if body.status == "published":
        await pipeline.mark_opportunity(session, p, "done")
    await session.commit()
    return await _out(session, p)


@router.post("/projects/{project_id}/generate", response_model=JobBrief, status_code=status.HTTP_202_ACCEPTED)
async def generate(project_id: int, body: GenerateIn, request: Request,
                   tenant: Tenant = Depends(require_role(Role.member)), session: AsyncSession = Depends(get_session)):
    p = await tenant.get(session, ContentProject, project_id)
    if body.action == "edit" and not (body.instruction or "").strip():
        raise HTTPException(422, "Напишите, что поправить")
    if body.action in ("edit", "qa") and await pipeline.latest(session, p.id) is None:
        raise HTTPException(422, "Сначала нужен черновик")
    return await _start(request, session, tenant, p, body.action, body.instruction)


@router.post("/projects/{project_id}/versions", response_model=VersionOut, status_code=status.HTTP_201_CREATED)
async def save_version(project_id: int, body: VersionIn, tenant: Tenant = Depends(require_role(Role.member)),
                       session: AsyncSession = Depends(get_session)):
    """Ручная правка — новая версия, проверка кодом сразу (без модели)."""
    p = await tenant.get(session, ContentProject, project_id)
    fields = {k: str(body.fields.get(k) or "").strip() for k in field_keys(p.format)}
    prev = await pipeline.latest(session, p.id)
    ctx = prev.context if prev else {}
    b = await brand.get(session, p.organization_id)
    result = {**qa.merge(qa.code_checks(p.format, fields, ctx, pipeline.allowed_facts(p, b, ctx)), None)}
    return await pipeline.add_version(session, p, "manual", fields, ctx, result, user_id=tenant.user.id)


@router.get("/projects/{project_id}/export")
async def export_version(project_id: int, type: Literal["md", "html"] = "md", version: int | None = None,
                         tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    p = await tenant.get(session, ContentProject, project_id)
    stmt = select(ContentVersion).where(ContentVersion.project_id == p.id)
    stmt = stmt.where(ContentVersion.number == version) if version else \
        stmt.order_by(ContentVersion.number.desc()).limit(1)
    v = (await session.execute(stmt)).scalar_one_or_none()
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Версия не найдена")
    body = export.markdown(p.format, v.fields) if type == "md" else export.to_html(p.format, v.fields)
    media = "text/markdown" if type == "md" else "text/html"
    return Response(body, media_type=f"{media}; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="content-{p.id}-v{v.number}.{type}"'})


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: int, tenant: Tenant = Depends(require_role(Role.member)),
                         session: AsyncSession = Depends(get_session)):
    await session.delete(await tenant.get(session, ContentProject, project_id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
