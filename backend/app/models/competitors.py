from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class Competitor(TimestampMixin, Base):
    """Конкурент организации. Его каналы — обычные `sources` с competitor_id (удаляются вместе с ним)."""
    __tablename__ = "competitors"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_competitors_org_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    website: Mapped[str | None] = mapped_column(String(1000))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # AI-профиль: {"stats": посчитано кодом, "ai": интерпретация модели}
    profile: Mapped[dict | None] = mapped_column(JSON)
    profile_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profile_model: Mapped[str | None] = mapped_column(String(120))
