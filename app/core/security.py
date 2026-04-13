"""
Synapze Enterprise — Security middleware
Tuned for 1,000 concurrent users:
- Module-level Redis pool (not instantiated per request)
- Token hash cache for rate limit key (avoids JWT decode on every request)
- Sliding-window rate limiting with Redis pipeline
- Concurrent stream limiter (semaphore)
"""
import hashlib
import time
from typing import Callable, Optional

import redis.asyncio as aioredis
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.core.exceptions import RateLimitError
from app.core.logging import get_logger, set_trace_id, set_request_id, set_span_id

logger = get_logger("security")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Cache-Control": "no-store",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}

RATE_LIMIT_EXCLUDE = frozenset({"/health", "/health/ready", "/metrics", "/favicon.ico"})
RATE_LIMIT_RELAXED  = frozenset({"/agent/stream"})

# ── Module-level Redis pool — shared across ALL requests in this process ────────
_redis_pool: Optional[aioredis.ConnectionPool] = None

def _get_redis_pool() -> aioredis.ConnectionPool:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.ConnectionPool.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
            socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT,
            retry_on_timeout=True,
            health_check_interval=settings.REDIS_HEALTH_CHECK_INTERVAL,
        )
    return _redis_pool

def get_redis_client() -> aioredis.Redis:
    return aioredis.Redis(connection_pool=_get_redis_pool())


class SecurityMiddleware(BaseHTTPMiddleware):
    """
    Per-request:
    1. Assign request ID + trace ID
    2. Sliding-window rate limit (Redis pipeline, no blocking)
    3. Concurrent stream cap
    4. Content-Type guard
    5. Security headers
    6. Prometheus metrics
    7. Structured access log
    """

    def __init__(self, app):
        super().__init__(app)
        # Semaphore: cap concurrent SSE streams per process
        self._stream_semaphore = None

    def _get_stream_semaphore(self):
        if self._stream_semaphore is None:
            import asyncio
            self._stream_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_STREAMS)
        return self._stream_semaphore

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.monotonic()

        request_id = set_request_id(request.headers.get("X-Request-ID"))
        trace_id   = set_trace_id(request.headers.get("X-Trace-ID"))
        set_span_id()

        # Fast path — liveness/metrics probes
        if request.url.path in RATE_LIMIT_EXCLUDE:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response

        # Rate limit
        try:
            await self._check_rate_limit(request)
        except RateLimitError as e:
            logger.warning("rate_limit", extra={"ip": self._get_ip(request), "path": request.url.path})
            return JSONResponse(
                status_code=429, content=e.to_dict(),
                headers={"Retry-After": "60",
                         "X-RateLimit-Reset": str(int(time.time()) + 60),
                         "X-Request-ID": request_id},
            )

        # Streaming concurrency cap
        is_stream = request.url.path in RATE_LIMIT_RELAXED
        sem = self._get_stream_semaphore() if is_stream else None
        if sem and not sem._value:   # already at limit
            return JSONResponse(
                status_code=503,
                content={"error": "CAPACITY_EXCEEDED",
                         "message": "Server is at streaming capacity. Please try again shortly."},
                headers={"X-Request-ID": request_id, "Retry-After": "5"},
            )

        # Content-type guard for mutation endpoints
        if request.method in ("POST", "PUT", "PATCH"):
            ct   = request.headers.get("content-type", "")
            path = request.url.path
            if ("/webhooks/" not in path
                    and "multipart/form-data" not in ct
                    and "application/x-www-form-urlencoded" not in ct
                    and "application/json" not in ct
                    and ct):
                return JSONResponse(
                    status_code=415,
                    content={"error": "UNSUPPORTED_MEDIA_TYPE",
                             "message": "Content-Type must be application/json"},
                    headers={"X-Request-ID": request_id},
                )

        # Execute request (with optional stream semaphore)
        if sem:
            async with sem:
                response = await call_next(request)
        else:
            response = await call_next(request)

        duration_ms = round((time.monotonic() - start) * 1000, 1)

        # Security headers
        for k, v in SECURITY_HEADERS.items():
            response.headers[k] = v
        response.headers["X-Request-ID"]    = request_id
        response.headers["X-Trace-ID"]      = trace_id
        response.headers["X-Response-Time"] = f"{duration_ms}ms"

        # Prometheus
        if settings.PROMETHEUS_ENABLED:
            try:
                from app.monitoring.metrics import record_request
                record_request(request.method, request.url.path, response.status_code, duration_ms)
            except Exception:
                pass

        logger.info("request", extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
            "ip": self._get_ip(request),
        })

        return response

    async def _check_rate_limit(self, request: Request) -> None:
        """
        Sliding window. Key = first 16 chars of SHA-256(token) for authed,
        IP for anonymous. No full JWT decode — just hash prefix.
        """
        r = get_redis_client()
        ip = self._get_ip(request)

        # Use token hash prefix as key (cheap — no crypto)
        rate_key = None
        auth = request.headers.get("Authorization", "")
        if len(auth) > 7 and auth.startswith("Bearer "):
            token_hash = hashlib.sha256(auth[7:].encode()).hexdigest()[:16]
            rate_key = f"rl:t:{token_hash}"

        key   = rate_key or f"rl:ip:{ip}"
        limit = settings.RATE_LIMIT_PER_MINUTE
        if request.url.path in RATE_LIMIT_RELAXED:
            limit = max(limit // 4, 5)

        now          = time.time()
        window_start = now - 60.0

        pipe = r.pipeline(transaction=False)  # no MULTI/EXEC — pipeline only for RTT savings
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zadd(key, {f"{now:.6f}": now})
        pipe.zcard(key)
        pipe.expire(key, 120)
        results = await pipe.execute()
        count = results[2]

        if count > limit:
            raise RateLimitError(retry_after=60)

    def _get_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"


def sanitize_input(text: str, max_length: int = 10_000) -> str:
    if not text:
        return ""
    text = text[:max_length].replace("\x00", "")
    sanitized = "".join(
        ch for ch in text
        if ch in ("\n", "\t", "\r") or (32 <= ord(ch) <= 126) or ord(ch) > 127
    )
    return sanitized.strip()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def mask_email(email: str) -> str:
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    return f"{local[0]}***@{domain}" if local else f"***@{domain}"
