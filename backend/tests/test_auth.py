from tests.conftest import register


async def test_register_creates_owner_org_and_free_plan(client):
    acc = await register(client, org="Кофейня «Утро»")
    me = (await acc.get("/api/v1/auth/me")).json()
    assert me["email"] == acc.email
    assert me["organizations"][0]["role"] == "owner"
    assert me["organizations"][0]["name"] == "Кофейня «Утро»"
    usage = (await acc.get("/api/v1/billing/usage")).json()
    assert usage["plan"] == "free"
    assert usage["used"] == {}


async def test_register_duplicate_email_case_insensitive(client):
    acc = await register(client)
    r = await client.post("/api/v1/auth/register", json={
        "email": acc.email.upper(), "password": "password123", "organization_name": "X"})
    assert r.status_code == 409


async def test_login_and_refresh(client):
    acc = await register(client)
    bad = await client.post("/api/v1/auth/login", json={"email": acc.email, "password": "wrong-password"})
    assert bad.status_code == 401
    ok = await client.post("/api/v1/auth/login", json={"email": acc.email, "password": "password123"})
    assert ok.status_code == 200
    tokens = ok.json()
    refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    # access-токен нельзя использовать как refresh и наоборот
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert r.status_code == 401
    r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"})
    assert r.status_code == 401


async def test_protected_without_token(client):
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/jobs")).status_code == 401


async def test_short_password_rejected(client):
    r = await client.post("/api/v1/auth/register", json={"email": "short@example.com", "password": "123",
                                                         "organization_name": "X"})
    assert r.status_code == 422


async def test_plans_seeded(client):
    codes = [p["code"] for p in (await client.get("/api/v1/billing/plans")).json()]
    assert codes == ["free", "starter", "professional", "agency", "enterprise"]
