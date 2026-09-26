"""Дайджест рынка: цифры периода (код) + выводы и идеи (модель). Расписание — одно на организацию."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class Digest(TimestampMixin, Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    period_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    days: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # manual | schedule
    stats: Mapped[dict] = mapped_column(JSON, default=dict)      # всё посчитано кодом
    sections: Mapped[dict] = mapped_column(JSON, default=dict)   # тексты: модель или шаблон по цифрам
    ai: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str | None] = mapped_column(String(120))
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_error: Mapped[str | None] = mapped_column(Text)


class DigestSchedule(Base):
    __tablename__ = "digest_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    period: Mapped[str] = mapped_column(String(10), default="weekly")  # weekly | monthly | custom
    weekday: Mapped[int] = mapped_column(Integer, default=0)   # weekly: 0 — понедельник
    day: Mapped[int] = mapped_column(Integer, default=1)       # monthly: число 1–28
    every_days: Mapped[int] = mapped_column(Integer, default=14)  # custom: раз в N дней, период = N дней
    hour: Mapped[int] = mapped_column(Integer, default=6)      # UTC
    send_email: Mapped[bool] = mapped_column(Boolean, default=False)
    recipients: Mapped[list] = mapped_column(JSON, default=list)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
