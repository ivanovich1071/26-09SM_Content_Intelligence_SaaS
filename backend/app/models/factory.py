"""Контент Завод: профиль и голос бренда, проекты материалов и их версии.

Версия неизменяема: черновик Writer, правка Editor и ручная правка — каждая новой версией. В версии хранится
контекст, на котором она написана (какие посты рынка, паттерны, аудит), и результат QA."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, utcnow


class BrandProfile(Base):
    """Профиль компании и голос бренда — одна запись на организацию."""
    __tablename__ = "brand_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), unique=True)
    company: Mapped[str | None] = mapped_column(String(200))
    website: Mapped[str | None] = mapped_column(String(1000))
    description: Mapped[str | None] = mapped_column(Text)      # чем занимается компания
    offer: Mapped[str | None] = mapped_column(Text)            # главное предложение клиенту
    audience: Mapped[str | None] = mapped_column(Text)
    differentiators: Mapped[list] = mapped_column(JSON, default=list)
    proof_points: Mapped[list] = mapped_column(JSON, default=list)  # факты, которые можно упоминать: кейсы, цифры
    cta: Mapped[str | None] = mapped_column(Text)              # основной призыв по умолчанию
    tone: Mapped[str | None] = mapped_column(Text)             # голос: как звучим
    do: Mapped[list] = mapped_column(JSON, default=list)
    dont: Mapped[list] = mapped_column(JSON, default=list)
    examples: Mapped[list] = mapped_column(JSON, default=list)  # образцы своих текстов
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | ai
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ContentProject(TimestampMixin, Base):
    __tablename__ = "content_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))        # тема
    brief: Mapped[str | None] = mapped_column(Text)       # пожелания автору
    format: Mapped[str] = mapped_column(String(20))       # telegram | email | linkedin | vk | article
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("content_opportunities.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | approved | published | archived
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ContentVersion(Base):
    __tablename__ = "content_versions"
    __table_args__ = (UniqueConstraint("project_id", "number", name="uq_content_versions_project_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("content_projects.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))           # write | edit | manual
    instruction: Mapped[str | None] = mapped_column(Text)   # что просили у редактора
    fields: Mapped[dict] = mapped_column(JSON)              # {subject, preheader, title, lead, text} — по формату
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    qa: Mapped[dict] = mapped_column(JSON, default=dict)    # {checks: [...], ai: bool, at}
    model: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
