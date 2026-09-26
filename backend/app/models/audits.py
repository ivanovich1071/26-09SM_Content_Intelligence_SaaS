"""Аудит контента. Результат самодостаточен (метрики, benchmark, gaps — в `result`), поэтому аудит, сделанный
публично без регистрации, можно перенести в организацию пользователя после регистрации."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class ContentAudit(TimestampMixin, Base):
    __tablename__ = "content_audits"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    company: Mapped[str] = mapped_column(String(200))
    website: Mapped[str | None] = mapped_column(String(1000))
    inputs: Mapped[list] = mapped_column(JSON, default=list)  # адреса каналов, введённые пользователем
    use_own_sources: Mapped[bool] = mapped_column(Boolean, default=False)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    stage: Mapped[str] = mapped_column(String(30), default="queued")
    score: Mapped[float | None] = mapped_column(Float)  # 0–100, взвешенная сумма критериев — считает код
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    model: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Публичный аудит (лид-магнит): ссылка по токену, лимит по IP/email
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    public_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    email: Mapped[str | None] = mapped_column(String(320), index=True)


class AuditItem(Base):
    """Оценка по одному критерию: балл 0–10 (или null — «недостаточно данных»), пояснение, доказательства, советы."""
    __tablename__ = "audit_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    audit_id: Mapped[int] = mapped_column(ForeignKey("content_audits.id", ondelete="CASCADE"), index=True)
    criterion: Mapped[str] = mapped_column(String(40))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float | None] = mapped_column(Float)
    weight: Mapped[float] = mapped_column(Float)
    explanation: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list] = mapped_column(JSON, default=list)          # [{fact, post_id?, url?}]
    recommendations: Mapped[list] = mapped_column(JSON, default=list)   # [str]
    ai: Mapped[bool] = mapped_column(Boolean, default=False)
