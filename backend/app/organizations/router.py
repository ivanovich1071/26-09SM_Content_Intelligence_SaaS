from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service
from app.auth.schemas import OrgBrief
from app.billing import quotas
from app.core.db import get_session, utcnow
from app.core.deps import Tenant, get_current_user, get_tenant, require_role
from app.models import Invitation, Membership, Organization, Role, User

router = APIRouter(prefix="/organizations", tags=["organizations"])


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class OrgUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class MemberOut(BaseModel):
    id: int
    user_id: int
    email: str
    full_name: str | None
    role: Role


class InviteIn(BaseModel):
    email: EmailStr
    role: Role = Role.member


class InvitationOut(BaseModel):
    id: int
    email: str
    role: Role
    created_at: datetime
    accepted_at: datetime | None


class RoleUpdate(BaseModel):
    role: Role


@router.get("", response_model=list[OrgBrief])
async def list_mine(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    return [OrgBrief(id=o.id, name=o.name, slug=o.slug, role=r)
            for o, r in await service.my_organizations(session, user.id)]


@router.post("", response_model=OrgBrief, status_code=status.HTTP_201_CREATED)
async def create(body: OrgCreate, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    org = await service.create_organization(session, body.name, user)
    await session.commit()
    return OrgBrief(id=org.id, name=org.name, slug=org.slug, role=Role.owner)


@router.get("/current", response_model=OrgBrief)
async def current(tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    org = await session.get(Organization, tenant.org_id)
    return OrgBrief(id=org.id, name=org.name, slug=org.slug, role=tenant.role)


@router.patch("/current", response_model=OrgBrief)
async def update_current(body: OrgUpdate, tenant: Tenant = Depends(require_role(Role.admin)),
                         session: AsyncSession = Depends(get_session)):
    org = await session.get(Organization, tenant.org_id)
    org.name = body.name
    await session.commit()
    return OrgBrief(id=org.id, name=org.name, slug=org.slug, role=tenant.role)


@router.get("/current/members", response_model=list[MemberOut])
async def members(tenant: Tenant = Depends(get_tenant), session: AsyncSession = Depends(get_session)):
    rows = await session.execute(
        tenant.scoped(select(Membership, User).join(User, User.id == Membership.user_id), Membership)
        .order_by(Membership.id))
    return [MemberOut(id=m.id, user_id=u.id, email=u.email, full_name=u.full_name, role=m.role) for m, u in rows]


async def _member_count(session: AsyncSession, org_id: int) -> int:
    return (await session.execute(select(func.count()).select_from(Membership)
                                  .where(Membership.organization_id == org_id))).scalar_one()


@router.post("/current/invitations", response_model=InvitationOut, status_code=status.HTTP_201_CREATED)
async def invite(body: InviteIn, tenant: Tenant = Depends(require_role(Role.admin)),
                 session: AsyncSession = Depends(get_session)):
    if body.role == Role.owner and tenant.role != Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Назначить владельца может только владелец")
    await quotas.check(session, tenant.org_id, "members", current=await _member_count(session, tenant.org_id))
    email = body.email.strip().lower()
    existing = (await session.execute(tenant.scoped(select(Invitation).where(Invitation.email == email), Invitation))
                ).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Приглашение уже отправлено")
    inv = Invitation(organization_id=tenant.org_id, email=email, role=body.role, invited_by=tenant.user.id)
    session.add(inv)
    # Если пользователь уже зарегистрирован — сразу добавляем в команду
    user = (await session.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
    if user:
        already = (await session.execute(tenant.scoped(
            select(Membership).where(Membership.user_id == user.id), Membership))).scalar_one_or_none()
        if already:
            raise HTTPException(status.HTTP_409_CONFLICT, "Пользователь уже в команде")
        session.add(Membership(organization_id=tenant.org_id, user_id=user.id, role=body.role))
        inv.accepted_at = utcnow()
    await session.commit()
    return InvitationOut(id=inv.id, email=inv.email, role=inv.role, created_at=inv.created_at,
                         accepted_at=inv.accepted_at)


@router.get("/current/invitations", response_model=list[InvitationOut])
async def invitations(tenant: Tenant = Depends(require_role(Role.admin)), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(tenant.scoped(select(Invitation), Invitation).order_by(Invitation.id))).scalars()
    return [InvitationOut(id=i.id, email=i.email, role=i.role, created_at=i.created_at, accepted_at=i.accepted_at)
            for i in rows]


async def _owners(session: AsyncSession, org_id: int) -> int:
    return (await session.execute(select(func.count()).select_from(Membership).where(
        Membership.organization_id == org_id, Membership.role == Role.owner))).scalar_one()


def _can_manage(tenant: Tenant, target: Membership, new_role: Role | None = None) -> None:
    touches_owner = target.role == Role.owner or new_role == Role.owner
    if touches_owner and tenant.role != Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Управлять владельцами может только владелец")


@router.patch("/current/members/{membership_id}", response_model=MemberOut)
async def change_role(membership_id: int, body: RoleUpdate, tenant: Tenant = Depends(require_role(Role.admin)),
                      session: AsyncSession = Depends(get_session)):
    m: Membership = await tenant.get(session, Membership, membership_id)
    _can_manage(tenant, m, body.role)
    if m.role == Role.owner and body.role != Role.owner and await _owners(session, tenant.org_id) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Нельзя убрать последнего владельца")
    m.role = body.role
    await session.commit()
    user = await session.get(User, m.user_id)
    return MemberOut(id=m.id, user_id=user.id, email=user.email, full_name=user.full_name, role=m.role)


@router.delete("/current/members/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(membership_id: int, tenant: Tenant = Depends(require_role(Role.admin)),
                        session: AsyncSession = Depends(get_session)):
    m: Membership = await tenant.get(session, Membership, membership_id)
    _can_manage(tenant, m)
    if m.role == Role.owner and await _owners(session, tenant.org_id) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Нельзя удалить последнего владельца")
    await session.delete(m)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
