from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service
from app.auth.schemas import LoginIn, MeOut, OrgBrief, RefreshIn, RegisterIn, TokenOut
from app.core.db import get_session
from app.core.deps import get_current_user
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


def _tokens(user_id: int) -> TokenOut:
    return TokenOut(access_token=create_token(user_id, "access"), refresh_token=create_token(user_id, "refresh"))


def _norm_email(email: str) -> str:
    return email.strip().lower()


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, session: AsyncSession = Depends(get_session)):
    email = _norm_email(body.email)
    if (await session.execute(select(User.id).where(func.lower(User.email) == email))).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Пользователь с таким email уже есть")
    user = User(email=email, password_hash=hash_password(body.password), full_name=body.full_name)
    session.add(user)
    await session.flush()
    await service.create_organization(session, body.organization_name, user)
    await service.accept_pending_invitations(session, user)
    await session.commit()
    return _tokens(user.id)


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, session: AsyncSession = Depends(get_session)):
    user = (await session.execute(select(User).where(func.lower(User.email) == _norm_email(body.email)))
            ).scalar_one_or_none()
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный email или пароль")
    return _tokens(user.id)


@router.post("/refresh", response_model=TokenOut)
async def refresh(body: RefreshIn, session: AsyncSession = Depends(get_session)):
    user_id = decode_token(body.refresh_token, "refresh")
    user = await session.get(User, user_id) if user_id else None
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла, войдите снова")
    return _tokens(user.id)


@router.get("/me", response_model=MeOut)
async def me(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    orgs = await service.my_organizations(session, user.id)
    return MeOut(id=user.id, email=user.email, full_name=user.full_name,
                 organizations=[OrgBrief(id=o.id, name=o.name, slug=o.slug, role=r) for o, r in orgs])
