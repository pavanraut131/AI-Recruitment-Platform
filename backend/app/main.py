from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import routes as composed
from app.modules.hiring import router as hiring
from app.modules.ingestion import router as ingestion
from app.platform.config import settings
from app.platform.csrf import HEADER as CSRF_HEADER
from app.platform.logging import configure_logging
from app.platform.storage import ensure_bucket_exists
from app.v2 import v2_app

configure_logging(debug=settings.DEBUG)
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown events."""
    logger.info("starting_up", env=settings.APP_ENV)
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
    # v1 is frozen: it has always parsed a JSON body sent without a Content-Type header.
    # FastAPI >= 0.132 rejects that by default (422); /api/v2 (its own app) keeps the strict
    # default.
    strict_content_type=False,
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

# ── Routers ───────────────────────────────────────────────────
PREFIX = settings.API_V1_PREFIX
# Module routers, plus the endpoints composed from several modules (app.api). No two routes
# share a path and method, so the order does not change which endpoint answers a request.
app.include_router(hiring.campaigns_router, prefix=PREFIX)
app.include_router(composed.campaigns_router, prefix=PREFIX)
app.include_router(hiring.parse_jd_router, prefix=PREFIX)
app.include_router(ingestion.router, prefix=PREFIX)
app.include_router(composed.candidates_router, prefix=PREFIX)
app.include_router(composed.stats_router, prefix=PREFIX)

# /api/v2: a sub-application with its own OpenAPI document (/api/v2/openapi.json) and RFC 9457
# error handlers, so the v1 routes, errors and /openapi.json above stay exactly as they were
# (see app/v2.py).
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


# ── Global error handler ──────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", path=str(request.url), error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "path": str(request.url)},
    )
