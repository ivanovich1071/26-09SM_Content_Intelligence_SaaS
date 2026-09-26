"""Доступ к админке — только суперадминам (users.is_superadmin). Остальным — 404: раздел не раскрывается."""
from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.models import AdminAction, User


async def get_superadmin(user: User = Depends(get_current_user)) -> User:
    if not user.is_superadmin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Не найдено")
    return user


def log_action(session: AsyncSession, admin: User, action: str, target_type: str, target_id: int, *,
               org_id: int | None = None, **details) -> None:
    """Запись в журнал; коммитит вызывающий вместе с самим изменением."""
    session.add(AdminAction(admin_id=admin.id, action=action, organization_id=org_id, target_type=target_type,
                            target_id=target_id, details=details))
