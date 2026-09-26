import logging
import time
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import text

from app.analysis.router import market_router
from app.analysis.router import router as taxonomy_router
from app.auth.router import router as auth_router
from app.billing.plans import sync_plans
from app.billing.router import router as billing_router
from app.competitors.router import router as competitors_router
from app.core.config import settings
from app.core.db import SessionLocal
from app.jobs.router import router as jobs_router
from app.organizations.router import router as org_router
from app.sources.router import router as sources_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sm")
STARTED = time.monotonic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with SessionLocal() as session:
        await sync_plans(session)
    try:
        app.state.arq = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    except Exception as e:  # noqa: BLE001 — API работает и без очереди, задачи остаются queued
        log.warning("Redis недоступен (%s): задачи не будут ставиться в очередь", e)
        app.state.arq = None
    yield
    if app.state.arq is not None:
        await app.state.arq.aclose()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

api = APIRouter(prefix="/api/v1")
for r in (auth_router, org_router, billing_router, jobs_router, sources_router, taxonomy_router,
          market_router, competitors_router):
    api.include_router(r)
app.include_router(api)


@app.get("/health")
async def health():
    checks: dict[str, str] = {}
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["db"] = f"error: {type(e).__name__}"
    try:
        redis = Redis.from_url(settings.redis_url)
        await redis.ping()
        await redis.aclose()
        checks["redis"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["redis"] = f"error: {type(e).__name__}"
    ok = all(v == "ok" for v in checks.values())
    return {"status": "ok" if ok else "degraded", "uptime_sec": int(time.monotonic() - STARTED), **checks}
