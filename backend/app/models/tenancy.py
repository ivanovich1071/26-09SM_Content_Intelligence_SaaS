import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin


class Role(enum.StrEnum):
    owner = "owner"
    admin = "admin"
    member = "member"
    viewer = "viewer"


ROLE_RANK = {Role.viewer: 0, Role.member: 1, Role.admin: 2, Role.owner: 3}


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)

    memberships: Mapped[list["Membership"]] = relationship(back_populates="organization", cascade="all, delete-orphan")


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    full_name: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # вход или обновление сессии

    memberships: Mapped[list["Membership"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Membership(TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_memberships_org_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[Role] = mapped_column(Enum(Role, name="member_role", values_callable=lambda e: [r.value for r in e]))

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class Invitation(TimestampMixin, Base):
    __tablename__ = "invitations"
    __table_args__ = (UniqueConstraint("organization_id", "email", name="uq_invitations_org_email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[Role] = mapped_column(Enum(Role, name="member_role", values_callable=lambda e: [r.value for r in e]))
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
