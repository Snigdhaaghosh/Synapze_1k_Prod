"""
Synapze Enterprise — Configuration
Tuned for 1,000+ concurrent users.
"""
from functools import lru_cache
from typing import Optional
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings
import multiprocessing


def _default_workers() -> int:
    """(2 × CPU cores) + 1, capped at 16."""
    return min((multiprocessing.cpu_count() * 2) + 1, 16)


class Settings(BaseSettings):
    # ── Core ──────────────────────────────────────────────────────────────────
    APP_NAME: str = "Synapze Enterprise"
    APP_ENV: str = "development"
    APP_VERSION: str = "2.1.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    # ── Security ──────────────────────────────────────────────────────────────
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_EXPIRE_DAYS: int = 30
    ALLOWED_ORIGINS: list[str] = ["http://localhost:5173"]
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_BURST: int = 20
    ENCRYPTION_KEY: str = ""
    MAX_REQUEST_SIZE_MB: int = 50
    TRUSTED_PROXIES: list[str] = ["172.16.0.0/12", "10.0.0.0/8"]

    # ── Anthropic ─────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"
    ANTHROPIC_MAX_TOKENS: int = 8192
    AGENT_MAX_ITERATIONS: int = 15
    AGENT_TOOL_TIMEOUT_SECS: int = 30
    ANTHROPIC_API_TIMEOUT_SECS: int = 90
    ANTHROPIC_MAX_RETRIES: int = 3
    # Semaphore: max concurrent Anthropic calls across the process
    ANTHROPIC_MAX_CONCURRENT: int = 50

    # ── Database ──────────────────────────────────────────────────────────────
    # Point to PgBouncer (port 5432 on pgbouncer service)
    DATABASE_URL: str
    DATABASE_URL_READ: Optional[str] = None
    # Per-process pool. With 4 workers: 4×25 = 100 → PgBouncer handles the rest
    DB_POOL_MIN: int = 5
    DB_POOL_MAX: int = 25
    DB_COMMAND_TIMEOUT: int = 20
    DB_STATEMENT_CACHE_SIZE: int = 0   # MUST be 0 when behind PgBouncer (transaction mode)
    DB_MAX_INACTIVE_LIFETIME: int = 60
    DB_MAX_CACHED_STATEMENT_LIFETIME: int = 0

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_URL: str
    REDIS_MAX_CONNECTIONS: int = 200   # shared pool across all coroutines in process
    REDIS_SOCKET_TIMEOUT: int = 3
    REDIS_CONNECT_TIMEOUT: int = 5
    REDIS_HEALTH_CHECK_INTERVAL: int = 30

    # ── Google OAuth ──────────────────────────────────────────────────────────
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/auth/google/callback"

    # ── Integrations ──────────────────────────────────────────────────────────
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_WHATSAPP_NUMBER: str = ""
    SLACK_BOT_TOKEN: str = ""
    SLACK_SIGNING_SECRET: str = ""
    OPENAI_API_KEY: str = ""
    HEYGEN_API_KEY: str = ""
    ELEVENLABS_API_KEY: str = ""
    FACEBOOK_PAGE_TOKEN: str = ""
    FACEBOOK_PAGE_ID: str = ""

    # ── Monitoring ────────────────────────────────────────────────────────────
    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.05   # 5% tracing in prod
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""
    PROMETHEUS_ENABLED: bool = True
    PROMETHEUS_SECRET: str = ""

    # ── Feature flags ─────────────────────────────────────────────────────────
    FEATURE_SEMANTIC_MEMORY: bool = True
    FEATURE_WORLD_MODEL: bool = True
    FEATURE_BROWSER_AGENT: bool = True
    FEATURE_PROACTIVE_MONITORS: bool = True

    # ── Server process tuning ─────────────────────────────────────────────────
    UVICORN_WORKERS: int = 0           # 0 = auto-detect from CPU count
    UVICORN_BACKLOG: int = 2048        # OS listen backlog for 1k users
    UVICORN_TIMEOUT_KEEP_ALIVE: int = 75
    UVICORN_LIMIT_CONCURRENCY: int = 0  # 0 = unlimited (nginx handles)
    UVICORN_LIMIT_MAX_REQUESTS: int = 10000  # restart worker after N requests (memory leak prevention)

    # ── Celery ────────────────────────────────────────────────────────────────
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""
    CELERY_TASK_SOFT_LIMIT: int = 300
    CELERY_TASK_HARD_LIMIT: int = 360
    CELERY_WORKER_CONCURRENCY: int = 16
    CELERY_MAX_TASKS_PER_CHILD: int = 500

    # ── Proactive monitors ────────────────────────────────────────────────────
    MONITOR_EMAIL_INTERVAL_MINS: int = 5
    MONITOR_CALENDAR_INTERVAL_MINS: int = 30
    YOUR_WHATSAPP_NUMBER: str = ""

    # ── Sandbox / Browser ─────────────────────────────────────────────────────
    SANDBOX_ENABLED: bool = False
    BROWSER_HEADLESS: bool = True
    SANDBOX_TIMEOUT_SECS: int = 30
    SANDBOX_MEMORY_MB: int = 256

    # ── Limits ────────────────────────────────────────────────────────────────
    MAX_HISTORY_MESSAGES: int = 50
    MAX_MEMORY_RESULTS: int = 20
    MAX_EMAIL_RESULTS: int = 50
    # Max concurrent streaming connections per process
    MAX_CONCURRENT_STREAMS: int = 200

    @field_validator("JWT_SECRET")
    @classmethod
    def jwt_secret_strong(cls, v: str) -> str:
        if len(v) < 64:
            raise ValueError("JWT_SECRET must be at least 64 characters")
        return v

    @field_validator("APP_ENV")
    @classmethod
    def valid_env(cls, v: str) -> str:
        if v not in ("development", "staging", "production", "test"):
            raise ValueError("APP_ENV must be development | staging | production | test")
        return v

    @model_validator(mode="after")
    def production_checks(self) -> "Settings":
        if self.APP_ENV == "production":
            errors = []
            if self.DEBUG:
                errors.append("DEBUG must be False in production")
            if "localhost" in self.GOOGLE_REDIRECT_URI:
                errors.append("GOOGLE_REDIRECT_URI must use real domain in production")
            if not self.ENCRYPTION_KEY:
                errors.append("ENCRYPTION_KEY must be set to encrypt OAuth tokens at rest")
            if not self.SENTRY_DSN:
                import warnings
                warnings.warn("SENTRY_DSN not set — errors won't be tracked in production", stacklevel=2)
            if errors:
                raise ValueError("Production config errors:\n  " + "\n  ".join(f"• {e}" for e in errors))
        return self

    @model_validator(mode="after")
    def set_celery_defaults(self) -> "Settings":
        if not self.CELERY_BROKER_URL:
            object.__setattr__(self, "CELERY_BROKER_URL", self.REDIS_URL)
        if not self.CELERY_RESULT_BACKEND:
            object.__setattr__(self, "CELERY_RESULT_BACKEND", self.REDIS_URL)
        if self.UVICORN_WORKERS == 0:
            object.__setattr__(self, "UVICORN_WORKERS", _default_workers())
        return self

    model_config = {"env_file": ".env", "case_sensitive": True, "extra": "ignore"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
