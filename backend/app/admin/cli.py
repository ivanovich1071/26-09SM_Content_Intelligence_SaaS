"""Выдать или снять права суперадмина из консоли:

    python -m app.admin.cli grant admin@company.by
    python -m app.admin.cli revoke admin@company.by
    python -m app.admin.cli list

В Docker: docker compose exec api python -m app.admin.cli grant admin@company.by"""
import asyncio
import sys

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import User


async def set_superadmin(email: str, value: bool) -> bool:
    async with SessionLocal() as session:
        user = (await session.execute(select(User).where(func.lower(User.email) == email.strip().lower()))
                ).scalar_one_or_none()
        if user is None:
            return False
        user.is_superadmin = value
        await session.commit()
        return True


async def superadmins() -> list[str]:
    async with SessionLocal() as session:
        return list((await session.execute(select(User.email).where(User.is_superadmin.is_(True))
                                           .order_by(User.email))).scalars())


def main(argv: list[str]) -> int:
    if argv[:1] == ["list"]:
        print("\n".join(asyncio.run(superadmins())) or "Суперадминов нет")
        return 0
    if len(argv) != 2 or argv[0] not in ("grant", "revoke"):
        print(__doc__)
        return 2
    if not asyncio.run(set_superadmin(argv[1], argv[0] == "grant")):
        print(f"Пользователь {argv[1]} не найден — сначала зарегистрируйтесь")
        return 1
    print("Готово")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
