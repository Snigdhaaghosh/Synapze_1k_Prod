"""
Synapze Enterprise — Health checks
/health          — basic liveness (no auth, used by load balancers)
/health/ready    — readiness: DB + Redis must be up
/health/detailed — full status (protected by internal token)
"""
import asyncio
import time
from typing import Any

from app.config import settings
from app.core.logging import get_logger

logger = get_logger("health")


async def check_database() -> dict:
    try:
        from app.db.database import get_pool
        pool = await get_pool()
        start = time.monotonic()
        await pool.fetchval("SELECT 1")
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        size = pool.get_size()
        return {"status": "ok", "latency_ms": latency_ms, "pool_size": size}
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def check_redis() -> dict:
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.REDIS_URL, socket_timeout=2)
        try:
            start = time.monotonic()
            await r.ping()
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            info = await r.info("memory")
            return {
                "status": "ok",
                "latency_ms": latency_ms,
                "used_memory_mb": round(info.get("used_memory", 0) / 1024 / 1024, 1),
            }
        finally:
            await r.aclose()
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def check_anthropic() -> dict:
    """Cheap check — just verify the API key is configured."""
    if not settings.ANTHROPIC_API_KEY:
        return {"status": "error", "error": "API key not configured"}
    if settings.ANTHROPIC_API_KEY.startswith("sk-ant-"):
        return {"status": "ok", "model": settings.ANTHROPIC_MODEL}
    return {"status": "warning", "message": "API key format unexpected"}


async def get_readiness() -> dict:
    """For Kubernetes readinessProbe — fails if any critical dependency is down."""
    db, redis = await asyncio.gather(check_database(), check_redis())
    all_ok = db["status"] == "ok" and redis["status"] == "ok"
    return {
        "ready": all_ok,
        "checks": {"database": db, "redis": redis},
    }


async def get_detailed_status() -> dict:
    db, redis, anthropic = await asyncio.gather(
        check_database(), check_redis(), check_anthropic()
    )
    return {
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "env": settings.APP_ENV,
        "checks": {
            "database": db,
            "redis": redis,
            "anthropic": anthropic,
        },
        "features": {
            "semantic_memory": settings.FEATURE_SEMANTIC_MEMORY and bool(settings.OPENAI_API_KEY),
            "world_model": settings.FEATURE_WORLD_MODEL,
            "browser_agent": settings.FEATURE_BROWSER_AGENT,
            "proactive_monitors": settings.FEATURE_PROACTIVE_MONITORS,
            "prometheus": settings.PROMETHEUS_ENABLED,
        },
    }
