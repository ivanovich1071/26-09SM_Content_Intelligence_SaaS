from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow


class PostInsight(Base):
    """AI-разбор поста. Кэшируется на организацию: разбор зависит от её ниши и оплачивается ею."""
    __tablename__ = "post_insights"
    __table_args__ = (UniqueConstraint("organization_id", "post_id", name="uq_post_insights_org_post"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("global_posts.id", ondelete="CASCADE"), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
