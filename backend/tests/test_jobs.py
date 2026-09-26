from app.models import Job
from app.workers.tasks import run_job
from tests.conftest import register


async def test_ping_job_runs_to_completion(client, session):
    acc = await register(client)
    job = (await acc.post("/api/v1/jobs/ping")).json()
    assert job["status"] == "queued"
    from app.workers.tasks import ping
    await ping({}, job["id"])
    done = (await acc.get(f"/api/v1/jobs/{job['id']}")).json()
    assert done["status"] == "completed" and done["progress"] == 100
    assert done["result"] == {"pong": True, "organization_id": acc.org_id}
    assert done["started_at"] and done["finished_at"]


async def test_failing_handler_marks_failed(client, session):
    acc = await register(client)
    job = (await acc.post("/api/v1/jobs/ping")).json()

    async def boom(_session, _job: Job) -> dict:
        raise RuntimeError("источник недоступен")

    await run_job(job["id"], boom)
    failed = (await acc.get(f"/api/v1/jobs/{job['id']}")).json()
    assert failed["status"] == "failed" and "источник недоступен" in failed["error"]


async def test_cancelled_job_is_not_run(client):
    acc = await register(client)
    job = (await acc.post("/api/v1/jobs/ping")).json()
    assert (await acc.post(f"/api/v1/jobs/{job['id']}/cancel")).json()["status"] == "cancelled"
    called = False

    async def handler(_s, _j):
        nonlocal called
        called = True
        return {}

    await run_job(job["id"], handler)
    assert not called
    assert (await acc.post(f"/api/v1/jobs/{job['id']}/cancel")).status_code == 409


async def test_health(client):
    body = (await client.get("/health")).json()
    assert body["db"] == "ok"
