"""Отслеживание сайтов: страницы, снимки текста и изменения «было/стало» со смыслом от модели."""
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, utcnow


class Website(TimestampMixin, Base):
    __tablename__ = "websites"
    __table_args__ = (UniqueConstraint("organization_id", "url", name="uq_websites_org_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    competitor_id: Mapped[int | None] = mapped_column(ForeignKey("competitors.id", ondelete="SET NULL"), index=True)
    url: Mapped[str] = mapped_column(String(1000))  # главная страница, нормализованная
    name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="new")  # new | ok | error
    last_error: Mapped[str | None] = mapped_column(Text)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class WebsitePage(Base):
    __tablename__ = "website_pages"
    __table_args__ = (UniqueConstraint("website_id", "url", name="uq_website_pages_site_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    website_id: Mapped[int] = mapped_column(ForeignKey("websites.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    # home | pricing | service | about | blog | article | contacts | other
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str | None] = mapped_column(String(500))
    tracked: Mapped[bool] = mapped_column(Boolean, default=True)
    status_code: Mapped[int | None] = mapped_column(Integer)
    last_hash: Mapped[str | None] = mapped_column(String(64))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PageSnapshot(Base):
    __tablename__ = "page_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("website_pages.id", ondelete="CASCADE"), index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    text_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(500))
    text: Mapped[str] = mapped_column(Text)


class WebsiteChange(Base):
    __tablename__ = "website_changes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    website_id: Mapped[int] = mapped_column(ForeignKey("websites.id", ondelete="CASCADE"), index=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("website_pages.id", ondelete="CASCADE"), index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(20))  # changed | new_page | removed_page
    added: Mapped[list] = mapped_column(JSON, default=list)
    removed: Mapped[list] = mapped_column(JSON, default=list)
    # Смысл изменения: модель или эвристика.
    # category: price | offer | product | positioning | contacts | content | other
    summary: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(20))
    importance: Mapped[str | None] = mapped_column(String(10))  # high | medium | low
    ai: Mapped[bool] = mapped_column(Boolean, default=False)
    before_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("page_snapshots.id", ondelete="SET NULL"))
    after_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("page_snapshots.id", ondelete="SET NULL"))
