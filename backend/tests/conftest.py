import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://sm:sm@localhost:5432/sm_test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-with-enough-length-32b")
os.environ["OPENROUTER_API_KEY"] = ""

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app import models  # noqa: E402, F401
from app.billing.plans import sync_plans  # noqa: E402
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
async def database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        await sync_plans(session)
    app.state.arq = None
    yield
    await engine.dispose()


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


class Account:
    def __init__(self, client: AsyncClient, email: str, token: str, org_id: int):
        self.client, self.email, self.token, self.org_id = client, email, token, org_id

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "X-Organization-Id": str(self.org_id)}

    async def get(self, url, **kw):
        return await self.client.get(url, headers=self.headers, **kw)

    async def post(self, url, **kw):
        return await self.client.post(url, headers=self.headers, **kw)

    async def patch(self, url, **kw):
        return await self.client.patch(url, headers=self.headers, **kw)

    async def delete(self, url, **kw):
        return await self.client.delete(url, headers=self.headers, **kw)


async def register(client: AsyncClient, org: str = "Org", email: str | None = None) -> Account:
    email = email or f"u-{uuid.uuid4().hex[:10]}@example.com"
    r = await client.post("/api/v1/auth/register",
                          json={"email": email, "password": "password123", "organization_name": org})
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    return Account(client, email, token, me.json()["organizations"][0]["id"])


async def set_plan(org_id: int, plan: str) -> None:
    from sqlalchemy import update

    from app.models import Subscription
    async with SessionLocal() as s:
        await s.execute(update(Subscription).where(Subscription.organization_id == org_id).values(plan_code=plan))
        await s.commit()
