"""Разметка и эмбеддинги постов.

Разметка зависит от таксономии организации, поэтому `post_analysis` — tenant-таблица. Эмбеддинг от организации
не зависит и хранится один раз на пост (`post_embeddings`)."""
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.db import Base, TimestampMixin, utcnow


class Taxonomy(TimestampMixin, Base):
    """Темы и роли аудитории организации. Универсальные поля (формат, воронка, CTA…) — в коде, одни на всех."""
    __tablename__ = "taxonomies"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), unique=True)
    topics: Mapped[list] = mapped_column(JSON, default=list)
    roles: Mapped[list] = mapped_column(JSON, default=list)
    niche: Mapped[str | None] = mapped_column(Text)  # одна фраза о нише — контекст для классификатора
    version: Mapped[int] = mapped_column(Integer, default=1)  # +1 при правке — посты старой версии переразмечаются
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | ai
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PostAnalysis(Base):
    __tablename__ = "post_analysis"
    __table_args__ = (UniqueConstraint("organization_id", "post_id", name="uq_post_analysis_org_post"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("global_posts.id", ondelete="CASCADE"), index=True)
    taxonomy_version: Mapped[int] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(String(120))
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error: Mapped[str | None] = mapped_column(Text)  # «нет текста», ошибка модели — пост пропускается в отчётах

    content_type: Mapped[str | None] = mapped_column(String(40))
    funnel_stage: Mapped[str | None] = mapped_column(String(40))
    hook_type: Mapped[str | None] = mapped_column(String(40))
    cta_type: Mapped[str | None] = mapped_column(String(40))
    proof_type: Mapped[str | None] = mapped_column(String(40))
    tone: Mapped[str | None] = mapped_column(String(40))
    value_type: Mapped[str | None] = mapped_column(String(40))
    topic: Mapped[str | None] = mapped_column(String(120), index=True)
    target_role: Mapped[str | None] = mapped_column(String(120))
    has_case: Mapped[bool | None] = mapped_column(Boolean)
    has_numbers: Mapped[bool | None] = mapped_column(Boolean)
    has_offer: Mapped[bool | None] = mapped_column(Boolean)
    has_lead_magnet: Mapped[bool | None] = mapped_column(Boolean)
    summary: Mapped[str | None] = mapped_column(Text)


class PostEmbedding(Base):
    __tablename__ = "post_embeddings"
    __table_args__ = (Index("ix_post_embeddings_hnsw", "embedding", postgresql_using="hnsw",
                            postgresql_ops={"embedding": "vector_cosine_ops"}),)

    post_id: Mapped[int] = mapped_column(ForeignKey("global_posts.id", ondelete="CASCADE"), primary_key=True)
    model: Mapped[str] = mapped_column(String(120))
    embedding = mapped_column(Vector(settings.embedding_dim), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
