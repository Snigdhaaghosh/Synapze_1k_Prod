"""
Synapze Enterprise — Structured logging with OpenTelemetry trace correlation.
Every log line is JSON. Every request has a trace_id + span_id.
Sentry integration for automatic error capture.
"""
import json
import logging
import sys
import traceback
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import settings

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")
_span_id: ContextVar[str] = ContextVar("span_id", default="")
_user_id: ContextVar[str] = ContextVar("user_id", default="")
_request_id: ContextVar[str] = ContextVar("request_id", default="")


def set_trace_id(tid: Optional[str] = None) -> str:
    tid = tid or str(uuid.uuid4()).replace("-", "")[:16]
    _trace_id.set(tid)
    return tid


def set_span_id() -> str:
    sid = str(uuid.uuid4()).replace("-", "")[:8]
    _span_id.set(sid)
    return sid


def set_user_id(uid: str) -> None:
    _user_id.set(uid)


def set_request_id(rid: Optional[str] = None) -> str:
    rid = rid or str(uuid.uuid4())
    _request_id.set(rid)
    return rid


def get_trace_id() -> str:
    return _trace_id.get()


def get_request_id() -> str:
    return _request_id.get()


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter compatible with Datadog, CloudWatch, GCP Logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            # Context vars — present on every log line within a request
            "trace_id": _trace_id.get() or None,
            "span_id": _span_id.get() or None,
            "request_id": _request_id.get() or None,
            "user_id": _user_id.get() or None,
            # Code location — helps with debugging
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            # App metadata
            "service": "synapze",
            "version": settings.APP_VERSION,
            "env": settings.APP_ENV,
        }

        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info),
            }

        # Merge any extra fields passed via logger.info("msg", extra={...})
        if hasattr(record, "extra"):
            log_data.update(record.extra)

        return json.dumps(log_data, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Human-readable formatter for local development."""

    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        ts = datetime.now().strftime("%H:%M:%S")
        tid = _trace_id.get()
        trace = f"[{tid}] " if tid else ""
        msg = record.getMessage()
        base = f"{color}{ts} {record.levelname:<8}{self.RESET} {trace}{record.name}: {msg}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging() -> None:
    """Configure root logger. Call once at startup."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    if settings.LOG_FORMAT == "text":
        handler.setFormatter(TextFormatter())
    else:
        handler.setFormatter(JSONFormatter())

    root.handlers.clear()
    root.addHandler(handler)

    # Silence noisy third-party libraries
    for lib in ("httpx", "httpcore", "asyncio", "multipart", "urllib3",
                "google.auth", "googleapiclient", "twilio"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    # Initialize Sentry if configured
    if settings.SENTRY_DSN:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.fastapi import FastApiIntegration
            from sentry_sdk.integrations.asyncio import AsyncioIntegration
            from sentry_sdk.integrations.redis import RedisIntegration
            from sentry_sdk.integrations.celery import CeleryIntegration

            sentry_sdk.init(
                dsn=settings.SENTRY_DSN,
                environment=settings.APP_ENV,
                release=settings.APP_VERSION,
                traces_sample_rate=0.1,       # 10% of requests traced
                profiles_sample_rate=0.05,
                integrations=[
                    FastApiIntegration(),
                    AsyncioIntegration(),
                    RedisIntegration(),
                    CeleryIntegration(),
                ],
                before_send=_scrub_sensitive_data,
            )
            logging.getLogger("synapze.main").info("Sentry initialized")
        except ImportError:
            logging.getLogger("synapze.main").warning(
                "sentry-sdk not installed — error tracking disabled"
            )


def _scrub_sensitive_data(event: dict, hint: dict) -> dict:
    """Remove sensitive fields before sending to Sentry."""
    sensitive_keys = {
        "access_token", "refresh_token", "jwt", "password",
        "api_key", "secret", "token", "authorization",
    }
    def _scrub(obj):
        if isinstance(obj, dict):
            return {
                k: "[REDACTED]" if k.lower() in sensitive_keys else _scrub(v)
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [_scrub(i) for i in obj]
        return obj
    return _scrub(event)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"synapze.{name}")
