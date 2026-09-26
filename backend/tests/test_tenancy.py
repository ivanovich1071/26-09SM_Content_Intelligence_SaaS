"""Изоляция организаций: пользователь B никогда не видит и не меняет данные организации A.
На каждый новый tenant-эндпоинт добавляйте проверку сюда."""
from tests.conftest import register


async def test_jobs_isolated(client):
    a = await register(client, "A")
    b = await register(client, "B")
    job = (await a.post("/api/v1/jobs/ping")).json()

    assert (await b.get(f"/api/v1/jobs/{job['id']}")).status_code == 404
    assert (await b.post(f"/api/v1/jobs/{job['id']}/cancel")).status_code == 404
    assert job["id"] not in [j["id"] for j in (await b.get("/api/v1/jobs")).json()]
    assert (await a.get(f"/api/v1/jobs/{job['id']}")).status_code == 200


async def test_foreign_org_header_is_404(client):
    a = await register(client, "A")
    b = await register(client, "B")
    headers = {"Authorization": f"Bearer {b.token}", "X-Organization-Id": str(a.org_id)}
    for url in ("/api/v1/organizations/current", "/api/v1/organizations/current/members", "/api/v1/jobs",
                "/api/v1/billing/usage", "/api/v1/sources", "/api/v1/taxonomy", "/api/v1/market/overview",
                "/api/v1/competitors", "/api/v1/posts"):
        assert (await client.get(url, headers=headers)).status_code == 404, url


async def test_members_isolated(client):
    a = await register(client, "A")
    b = await register(client, "B")
    a_member_id = (await a.get("/api/v1/organizations/current/members")).json()[0]["id"]

    assert (await b.patch(f"/api/v1/organizations/current/members/{a_member_id}", json={"role": "viewer"})
            ).status_code == 404
    assert (await b.delete(f"/api/v1/organizations/current/members/{a_member_id}")).status_code == 404
    emails = [m["email"] for m in (await b.get("/api/v1/organizations/current/members")).json()]
    assert emails == [b.email]


async def test_usage_isolated(client, session):
    from app.billing import usage

    a = await register(client, "A")
    b = await register(client, "B")
    await usage.record(session, a.org_id, "ai_cost_usd", "test", 0.1)
    assert (await a.get("/api/v1/billing/usage")).json()["used"] == {"ai_cost_usd": 0.1}
    assert (await b.get("/api/v1/billing/usage")).json()["used"] == {}


async def test_user_in_two_orgs_switches_by_header(client):
    a = await register(client, "A")
    second = (await a.post("/api/v1/organizations", json={"name": "Клиент агентства"})).json()
    job = (await a.post("/api/v1/jobs/ping")).json()
    headers = {"Authorization": f"Bearer {a.token}", "X-Organization-Id": str(second["id"])}
    assert (await client.get(f"/api/v1/jobs/{job['id']}", headers=headers)).status_code == 404
    assert (await client.get("/api/v1/organizations/current", headers=headers)).json()["name"] == "Клиент агентства"


async def test_sources_isolated(client):
    a = await register(client, "A")
    b = await register(client, "B")
    src = (await a.post("/api/v1/sources", json={"url": "@isolation_check"})).json()
    base = f"/api/v1/sources/{src['id']}"

    for url in (base, f"{base}/status", f"{base}/posts"):
        assert (await b.get(url)).status_code == 404, url
    assert (await b.post(f"{base}/sync")).status_code == 404
    assert (await b.patch(base, json={"name": "чужой"})).status_code == 404
    assert (await b.delete(base)).status_code == 404
    assert (await b.get("/api/v1/sources")).json() == []
    assert (await a.get(base)).json()["name"] is None
