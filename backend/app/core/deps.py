"""Зависимости FastAPI: текущий пользователь, тенант (организация) и проверка роли.

Все запросы к tenant-таблицам идут через `Tenant.scoped()` — он добавляет фильтр по organization_id.
Чужой ресурс отдаёт 404, а не 403, чтобы не раскрывать его существование."""
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import decode_token
from app.models import ROLE_RANK, Membership, Role, User

bearer = HTTPBearer(auto_error=False)


async def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                           session: AsyncSession = Depends(get_session)) -> User:
    user_id = decode_token(creds.credentials, "access") if creds else None
    user = await session.get(User, user_id) if user_id else None
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужна авторизация", headers={"WWW-Authenticate": "Bearer"})
    return user


@dataclass(frozen=True)
class Tenant:
    org_id: int
    user: User
    role: Role

    def scoped(self, stmt: Select, model) -> Select:
        return stmt.where(model.organization_id == self.org_id)

    async def get(self, session: AsyncSession, model, obj_id: int):
        obj = (await session.execute(self.scoped(select(model).where(model.id == obj_id), model))).scalar_one_or_none()
        if obj is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Не найдено")
        return obj

    def has_role(self, minimum: Role) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[minimum]


async def get_tenant(x_organization_id: int | None = Header(default=None),
                     user: User = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)) -> Tenant:
    stmt = select(Membership).where(Membership.user_id == user.id)
    if x_organization_id is not None:
        stmt = stmt.where(Membership.organization_id == x_organization_id)
    membership = (await session.execute(stmt.order_by(Membership.id).limit(1))).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Организация не найдена")
    return Tenant(org_id=membership.organization_id, user=user, role=membership.role)


def require_role(minimum: Role):
    async def dep(tenant: Tenant = Depends(get_tenant)) -> Tenant:
        if not tenant.has_role(minimum):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
        return tenant
    return dep
