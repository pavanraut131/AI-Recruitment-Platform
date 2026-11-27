from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.platform import database
from app.platform.config import settings
from app.platform.csrf import HEADER as CSRF_HEADER
from app.platform.logging import configure_logging
from app.platform.rls import check_app_role
from app.platform.storage import ensure_bucket_exists
from app.v2 import v2_app

configure_logging(debug=settings.DEBUG)
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown events."""
    logger.info("starting_up", env=settings.APP_ENV)
    # Refuse to serve as a database user that would bypass row-level security (a superuser,
    # BYPASSRLS, a table owner): EPIC-02 spec §6.2. Raises UnsafeDatabaseRole, which stops
    # the server before it accepts a request.
    async with database.engine.connect() as conn:
        role = await conn.run_sync(check_app_role, "the API")
    logger.info("database_role_checked", role=role)
    # Ensure MinIO bucket exists
    try:
        ensure_bucket_exists()
    except Exception as e:
        logger.warning("bucket_init_failed", error=str(e))
    yield
    logger.info("shutting_down")


app = FastAPI(
    title="AI Resume Bulk Import System",
    description="Bulk upload resumes, extract candidate info with AI, and rank candidates.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────
# Explicit origins (never "*") with credentials, so the browser sends the session cookie from
# the frontend's origin; only the headers the client sends (EPIC-02 spec §4.5). X-CSRF-Token is
# not a "simple" header, so every unsafe request is preflighted and refused for other origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", CSRF_HEADER],
    expose_headers=["Retry-After"],
)

# ── The API ───────────────────────────────────────────────────
# /api/v2: a sub-application with its own OpenAPI document (/api/v2/openapi.json), its
# authentication and its RFC 9457 error handlers (see app/v2.py). It is the only API: /api/v1
# was removed in E2-12, so its paths answer 404 like any other unknown path.
app.mount(settings.API_V2_PREFIX, v2_app)


# ── Health endpoints ──────────────────────────────────────────
@app.get("/health", tags=["health"])
async def health_check():
    return {"status": "ok", "version": "1.0.0", "env": settings.APP_ENV}


@app.get("/health/ready", tags=["health"])
async def readiness_check():
    """Check database and Redis connectivity."""
    checks = {}
    try:
        import redis

        r = redis.Redis.from_url(settings.REDIS_URL)
        r.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    try:
        from app.platform.database import engine

        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ready" if all_ok else "degraded", "checks": checks},
    )
