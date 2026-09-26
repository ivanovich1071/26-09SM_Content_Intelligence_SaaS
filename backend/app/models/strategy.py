"""Content Opportunities — темы, о которых стоит писать: почему, доказательства рынка, пробел клиента, примеры.

Цифры и примеры (`market`, `examples`, `score`) считает код по темам организации; формулировку, угол и форматы
даёт Content Strategist. Новая генерация архивирует прежние необработанные темы, взятые в работу — остаются."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, utcnow


class ContentOpportunity(TimestampMixin, Base):
    __tablename__ = "content_opportunities"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    audit_id: Mapped[int | None] = mapped_column(ForeignKey("content_audits.id", ondelete="SET NULL"))
    rank: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    topic: Mapped[str | None] = mapped_column(String(120))  # тема таксономии, из которой выросла идея
    why: Mapped[str] = mapped_column(Text, default="")
    angle: Mapped[str] = mapped_column(Text, default="")
    formats: Mapped[list] = mapped_column(JSON, default=list)
    funnel_stage: Mapped[str | None] = mapped_column(String(40))
    target_role: Mapped[str | None] = mapped_column(String(120))
    fixes: Mapped[list] = mapped_column(JSON, default=list)      # ключи критериев аудита, которые тема закрывает
    market: Mapped[dict] = mapped_column(JSON, default=dict)     # доли, gap, тренд, ER, насыщенность, конкуренты
    examples: Mapped[list] = mapped_column(JSON, default=list)   # лучшие публикации рынка по теме
    score: Mapped[float] = mapped_column(Float, default=0)
    ai: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)  # new|in_factory|done|dismissed|archived
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
