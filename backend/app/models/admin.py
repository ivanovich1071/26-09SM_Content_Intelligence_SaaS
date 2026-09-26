from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow


class AdminAction(Base):
    """Журнал действий суперадминов: смена тарифа, блокировка пользователя, перезапуск задачи."""
    __tablename__ = "admin_actions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(60))
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"),
                                                        index=True)
    target_type: Mapped[str] = mapped_column(String(40))  # organization | user | job
    target_id: Mapped[int] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
