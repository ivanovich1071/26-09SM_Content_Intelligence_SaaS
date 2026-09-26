"""Общие операции подключения источника — для /sources и для конкурентов."""
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import detect_kind, get_connector
from app.jobs import service as jobs
from app.models import GlobalSource, Source, SourceKind, SourceStatus


def resolve(url: str, kind: SourceKind | None = None) -> tuple[SourceKind, str, str]:
    """Адрес → (тип, ключ, URL) без сети. InvalidSource при неверном адресе."""
    kind = kind or SourceKind(detect_kind(url))
    key, norm_url = get_connector(kind).normalize(url)
    return kind, key, norm_url


async def count(session: AsyncSession, org_id: int) -> int:
    return (await session.execute(
        select(func.count()).select_from(Source).where(Source.organization_id == org_id))).scalar_one()


async def global_source(session: AsyncSession, kind: SourceKind, key: str, url: str) -> GlobalSource:
    await session.execute(insert(GlobalSource).values(kind=kind, key=key, url=url, status=SourceStatus.new, meta={})
                          .on_conflict_do_nothing(constraint="uq_global_sources_kind_key"))
    return (await session.execute(
        select(GlobalSource).where(GlobalSource.kind == kind, GlobalSource.key == key))).scalar_one()


async def existing(session: AsyncSession, org_id: int, gs_id: int) -> Source | None:
    return (await session.execute(select(Source).where(
        Source.organization_id == org_id, Source.global_source_id == gs_id))).scalar_one_or_none()


async def start_sync(session: AsyncSession, pool, org_id: int, source: Source, user_id: int | None) -> None:
    job = await jobs.create_job(session, org_id, "sync_source", {"source_id": source.id}, user_id=user_id)
    await jobs.enqueue(pool, job)
