from tests.conftest import Account, register, set_plan


async def _join(client, owner: Account, role: str) -> Account:
    """Регистрирует пользователя и добавляет его в организацию owner с ролью role."""
    user = await register(client, "Своя")
    r = await owner.post("/api/v1/organizations/current/invitations", json={"email": user.email, "role": role})
    assert r.status_code == 201, r.text
    return Account(client, user.email, user.token, owner.org_id)


async def test_free_plan_limits_members(client):
    owner = await register(client)
    r = await owner.post("/api/v1/organizations/current/invitations", json={"email": "x@example.com"})
    assert r.status_code == 402
    assert r.json()["detail"]["metric"] == "members"


async def test_viewer_cannot_run_jobs_or_invite(client):
    owner = await register(client)
    await set_plan(owner.org_id, "professional")
    viewer = await _join(client, owner, "viewer")
    assert (await viewer.get("/api/v1/jobs")).status_code == 200
    assert (await viewer.post("/api/v1/jobs/ping")).status_code == 403
    assert (await viewer.post("/api/v1/organizations/current/invitations", json={"email": "y@example.com"})
            ).status_code == 403


async def test_admin_cannot_manage_owners(client):
    owner = await register(client)
    await set_plan(owner.org_id, "professional")
    admin = await _join(client, owner, "admin")
    owner_mid = next(m["id"] for m in (await owner.get("/api/v1/organizations/current/members")).json()
                     if m["role"] == "owner")
    assert (await admin.post("/api/v1/organizations/current/invitations", json={"email": "o@example.com",
                                                                               "role": "owner"})).status_code == 403
    assert (await admin.patch(f"/api/v1/organizations/current/members/{owner_mid}", json={"role": "member"})
            ).status_code == 403
    assert (await admin.delete(f"/api/v1/organizations/current/members/{owner_mid}")).status_code == 403


async def test_last_owner_protected(client):
    owner = await register(client)
    mid = (await owner.get("/api/v1/organizations/current/members")).json()[0]["id"]
    assert (await owner.patch(f"/api/v1/organizations/current/members/{mid}", json={"role": "admin"})
            ).status_code == 409
    assert (await owner.delete(f"/api/v1/organizations/current/members/{mid}")).status_code == 409


async def test_invitation_accepted_on_register(client):
    owner = await register(client)
    await set_plan(owner.org_id, "starter")
    email = "invited-later@example.com"
    assert (await owner.post("/api/v1/organizations/current/invitations", json={"email": email, "role": "member"})
            ).status_code == 201
    newcomer = await register(client, "Своя", email=email)
    orgs = (await newcomer.get("/api/v1/auth/me")).json()["organizations"]
    assert {o["id"]: o["role"] for o in orgs}[owner.org_id] == "member"
