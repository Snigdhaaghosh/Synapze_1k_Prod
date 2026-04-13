"""
Synapze Enterprise — Application entry point
Tuned for 1,000 concurrent users.
"""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.core.exceptions import SynapzeError
from app.core.logging import setup_logging, get_logger
from app.core.security import SecurityMiddleware, _get_redis_pool
from app.db.database import init_db, close_db
from app.routes.routes import agent_router, auth_router, tasks_router, webhooks_router
from app.tasks.worker import celery_app  # noqa

setup_logging()
logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Synapze Enterprise starting", extra={
        "env": settings.APP_ENV,
        "version": settings.APP_VERSION,
        "workers": settings.UVICORN_WORKERS,
        "db_pool_max": settings.DB_POOL_MAX,
        "redis_max_connections": settings.REDIS_MAX_CONNECTIONS,
        "anthropic_max_concurrent": settings.ANTHROPIC_MAX_CONCURRENT,
        "max_concurrent_streams": settings.MAX_CONCURRENT_STREAMS,
    })

    # Initialize DB pool
    await init_db()

    # Warm up Redis pool (eager connect, not lazy)
    try:
        pool = _get_redis_pool()
        from app.core.security import get_redis_client
        r = get_redis_client()
        await r.ping()
        logger.info("Redis pool ready")
    except Exception as e:
        logger.error(f"Redis warmup failed: {e}")

    # Warm up Anthropic client singleton
    from app.agent.core import _get_client
    _get_client()
    logger.info("Ready")

    yield

    # Graceful shutdown — drain in-flight requests first
    logger.info("Shutdown initiated")
    await asyncio.sleep(0.5)  # allow in-flight requests to complete
    await close_db()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    description="Autonomous AI co-worker — Email · Calendar · WhatsApp · Slack · Browser",
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
)

# ── Middleware ─────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Trace-ID"],
    expose_headers=["X-Request-ID", "X-Trace-ID", "X-Response-Time"],
)

app.add_middleware(SecurityMiddleware)

# ── Routes ─────────────────────────────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(agent_router)
app.include_router(tasks_router)
app.include_router(webhooks_router)

# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok", "version": settings.APP_VERSION, "env": settings.APP_ENV}


@app.get("/health/ready", include_in_schema=False)
async def readiness():
    from app.health.checks import get_readiness
    result = await get_readiness()
    return JSONResponse(content=result, status_code=200 if result["ready"] else 503)


@app.get("/health/detailed", include_in_schema=False)
async def health_detailed(request: Request):
    token    = request.headers.get("X-Internal-Token", "")
    expected = settings.PROMETHEUS_SECRET
    if expected and token != expected:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    from app.health.checks import get_detailed_status
    return await get_detailed_status()


@app.get("/metrics", include_in_schema=False)
async def metrics(request: Request):
    if not settings.PROMETHEUS_ENABLED:
        return JSONResponse(status_code=404, content={"error": "Metrics disabled"})
    if settings.PROMETHEUS_SECRET:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {settings.PROMETHEUS_SECRET}":
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    from app.monitoring.metrics import get_metrics_output
    content, content_type = get_metrics_output()
    return Response(content=content, media_type=content_type)

# ── Exception handlers ─────────────────────────────────────────────────────────

@app.exception_handler(SynapzeError)
async def synapze_error_handler(request: Request, exc: SynapzeError):
    codes = {
        "AUTH_ERROR": 401, "TOKEN_EXPIRED": 401, "TOKEN_REVOKED": 401,
        "INSUFFICIENT_PERMISSIONS": 403, "ACCOUNT_SUSPENDED": 403,
        "NOT_FOUND": 404, "RATE_LIMIT": 429, "VALIDATION_ERROR": 422,
    }
    return JSONResponse(status_code=codes.get(exc.code, 400), content=exc.to_dict())


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception", exc_info=True, extra={
        "path": request.url.path, "method": request.method,
    })
    if settings.SENTRY_DSN:
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except Exception:
            pass
    return JSONResponse(
        status_code=500,
        content={"error": "INTERNAL_ERROR",
                 "message": "An internal error occurred. It has been logged."},
    )
