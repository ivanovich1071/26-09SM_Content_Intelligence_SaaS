import re
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.plans import DEFAULT_PLAN
from app.core.db import utcnow
from app.models import Invitation, Membership, Organization, Role, Subscription, User


def slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "org"
    return f"{base}-{secrets.token_hex(3)}"


async def create_organization(session: AsyncSession, name: str, owner: User) -> Organization:
    org = Organization(name=name, slug=slugify(name))
    session.add(org)
    await session.flush()
    session.add(Membership(organization_id=org.id, user_id=owner.id, role=Role.owner))
    session.add(Subscription(organization_id=org.id, plan_code=DEFAULT_PLAN))
    return org


async def accept_pending_invitations(session: AsyncSession, user: User) -> None:
    invites = (await session.execute(
        select(Invitation).where(Invitation.email == user.email, Invitation.accepted_at.is_(None)))).scalars()
    for inv in invites:
        session.add(Membership(organization_id=inv.organization_id, user_id=user.id, role=inv.role))
        inv.accepted_at = utcnow()


async def my_organizations(session: AsyncSession, user_id: int) -> list[tuple[Organization, Role]]:
    rows = await session.execute(
        select(Organization, Membership.role).join(Membership, Membership.organization_id == Organization.id)
        .where(Membership.user_id == user_id).order_by(Membership.id))
    return [(org, role) for org, role in rows]
