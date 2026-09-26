"""Источники и посты.

Публичный канал или сайт собирается один раз на всех клиентов: `global_sources` + `global_posts`.
Организация подключает его через `sources` (tenant-таблица) — там её роль источника и название."""
import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin, utcnow


class SourceKind(enum.StrEnum):
    telegram = "telegram"
    website = "website"
    rss = "rss"
    instagram = "instagram"
    youtube = "youtube"
    vk = "vk"


class SourceRole(enum.StrEnum):
    own = "own"                # свой канал/сайт организации — для аудита и сравнения с рынком
    competitor = "competitor"
    market = "market"          # отраслевые медиа, лидеры мнений


class SourceStatus(enum.StrEnum):
    new = "new"
    ok = "ok"
    error = "error"
    unavailable = "unavailable"  # не публичный канал, 404 и т.п. — повтор не поможет без правки адреса


def _enum(e: type[enum.Enum], name: str) -> Enum:
    return Enum(e, name=name, values_callable=lambda x: [s.value for s in x])


class GlobalSource(TimestampMixin, Base):
    __tablename__ = "global_sources"
    __table_args__ = (UniqueConstraint("kind", "key", name="uq_global_sources_kind_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[SourceKind] = mapped_column(_enum(SourceKind, "source_kind"))
    key: Mapped[str] = mapped_column(String(500))  # хэндл канала или нормализованный URL
    url: Mapped[str] = mapped_column(String(1000))
    title: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    followers: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[SourceStatus] = mapped_column(_enum(SourceStatus, "source_status"), default=SourceStatus.new)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # feed_url, social_links и др. данные коннектора
    # Базовая линия источника за 90 дней (медиана, а не среднее: один вирусный пост не искажает картину)
    median_views: Mapped[float | None] = mapped_column(Float)
    median_engagement: Mapped[float | None] = mapped_column(Float)
    metrics_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Source(TimestampMixin, Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("organization_id", "global_source_id", name="uq_sources_org_global"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    global_source_id: Mapped[int] = mapped_column(ForeignKey("global_sources.id", ondelete="RESTRICT"), index=True)
    role: Mapped[SourceRole] = mapped_column(_enum(SourceRole, "source_role"), default=SourceRole.market)
    name: Mapped[str | None] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    competitor_id: Mapped[int | None] = mapped_column(ForeignKey("competitors.id", ondelete="CASCADE"), index=True)

    global_source: Mapped[GlobalSource] = relationship(lazy="joined")


class GlobalPost(Base):
    __tablename__ = "global_posts"
    __table_args__ = (UniqueConstraint("global_source_id", "external_id", name="uq_global_posts_source_ext"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    global_source_id: Mapped[int] = mapped_column(ForeignKey("global_sources.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(500))
    url: Mapped[str | None] = mapped_column(String(1000))
    canonical_url: Mapped[str | None] = mapped_column(String(1000), index=True)
    author: Mapped[str | None] = mapped_column(String(300))
    title: Mapped[str | None] = mapped_column(String(1000))
    text: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    media_type: Mapped[str] = mapped_column(String(20), default="text")  # text|photo|video|album|document|poll
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    links: Mapped[list] = mapped_column(JSON, default=list)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    views: Mapped[int | None] = mapped_column(BigInteger)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments: Mapped[int | None] = mapped_column(BigInteger)
    shares: Mapped[int | None] = mapped_column(BigInteger)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metrics_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Считается кодом в analysis.metrics после каждого сбора
    engagement: Mapped[int | None] = mapped_column(BigInteger)       # реакции + комментарии + репосты
    er: Mapped[float | None] = mapped_column(Float)                  # engagement / views, %
    overperformance: Mapped[float | None] = mapped_column(Float)     # во сколько раз пост лучше медианы источника
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("global_posts.id", ondelete="SET NULL"))


class PostMetric(Base):
    """Снимки метрик поста при каждом сборе — для динамики просмотров и расчёта ER в EPIC 3."""
    __tablename__ = "post_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("global_posts.id", ondelete="CASCADE"), index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    views: Mapped[int | None] = mapped_column(BigInteger)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments: Mapped[int | None] = mapped_column(BigInteger)
    shares: Mapped[int | None] = mapped_column(BigInteger)
