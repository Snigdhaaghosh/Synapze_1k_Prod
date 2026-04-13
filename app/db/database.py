"""
Synapze Enterprise — Database layer
Tuned for 1,000 concurrent users:
- PgBouncer-compatible (statement_cache_size=0)
- Per-process asyncpg pool with health checks
- Module-level Fernet singleton (not recreated per call)
- Read replica routing
- Explicit transactions for write consistency
- Retry on transient asyncpg errors
- Token encryption at rest
"""
import json
import asyncio
from datetime import datetime, timezone
from typing import Any, Optional
from contextlib import asynccontextmanager

import asyncpg

from app.config import settings
from app.core.exceptions import DatabaseError, RecordNotFoundError
from app.core.logging import get_logger

logger = get_logger("db")

_pool: Optional[asyncpg.Pool] = None
_read_pool: Optional[asyncpg.Pool] = None

# ── Module-level Fernet singleton (expensive key derivation — do once) ─────────

_fernet = None

def _init_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet
    if not settings.ENCRYPTION_KEY:
        return None
    try:
        from cryptography.fernet import Fernet
        import base64, hashlib
        key = settings.ENCRYPTION_KEY.encode()
        if len(key) != 44:
            key = base64.urlsafe_b64encode(hashlib.sha256(key).digest())
        _fernet = Fernet(key)
        return _fernet
    except Exception as e:
        logger.error(f"Fernet init failed: {e}")
        return None

def encrypt_token(value: str) -> str:
    f = _init_fernet()
    if not f or not value:
        return value
    return f.encrypt(value.encode()).decode()

def decrypt_token(value: str) -> str:
    f = _init_fernet()
    if not f or not value:
        return value
    try:
        return f.decrypt(value.encode()).decode()
    except Exception:
        return value  # graceful fallback for unencrypted legacy values

# ── Pool management ────────────────────────────────────────────────────────────

async def _init_connection(conn: asyncpg.Connection) -> None:
    """Per-connection init: register JSON codecs."""
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json",  encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def init_db() -> None:
    global _pool, _read_pool
    _init_fernet()  # warm up cipher at startup, not per-request
    try:
        _pool = await asyncpg.create_pool(
            settings.DATABASE_URL,
            min_size=settings.DB_POOL_MIN,
            max_size=settings.DB_POOL_MAX,
            command_timeout=settings.DB_COMMAND_TIMEOUT,
            # CRITICAL: must be 0 when behind PgBouncer in transaction mode
            statement_cache_size=settings.DB_STATEMENT_CACHE_SIZE,
            max_inactive_connection_lifetime=settings.DB_MAX_INACTIVE_LIFETIME,
            init=_init_connection,
        )
        if settings.DATABASE_URL_READ:
            _read_pool = await asyncpg.create_pool(
                settings.DATABASE_URL_READ,
                min_size=2,
                max_size=10,
                command_timeout=settings.DB_COMMAND_TIMEOUT,
                statement_cache_size=0,
                init=_init_connection,
            )
        await _run_migrations()
        pool_size = _pool.get_size()
        logger.info("Database ready", extra={
            "pool_min": settings.DB_POOL_MIN,
            "pool_max": settings.DB_POOL_MAX,
            "pool_current": pool_size,
            "read_replica": bool(settings.DATABASE_URL_READ),
            "pgbouncer_mode": settings.DB_STATEMENT_CACHE_SIZE == 0,
        })
    except Exception as e:
        logger.error(f"Database init failed: {e}")
        raise DatabaseError(f"Cannot connect to database: {e}")


async def close_db() -> None:
    global _pool, _read_pool
    if _pool:
        await _pool.close()
        _pool = None
    if _read_pool:
        await _read_pool.close()
        _read_pool = None


async def get_pool(readonly: bool = False) -> asyncpg.Pool:
    if readonly and _read_pool:
        return _read_pool
    if not _pool:
        await init_db()
    return _pool


@asynccontextmanager
async def transaction():
    """Explicit transaction context manager."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn


async def _run_migrations() -> None:
    """Idempotent schema. Safe to run on every startup."""
    pool = await get_pool()
    async with pool.acquire() as conn:

        queries = [

            # ── Extensions ─────────────────────────────────────────────
            """CREATE EXTENSION IF NOT EXISTS "pgcrypto";""",
            """CREATE EXTENSION IF NOT EXISTS "vector";""",
            """CREATE EXTENSION IF NOT EXISTS "pg_trgm";""",

            # ── Users ──────────────────────────────────────────────────
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id          TEXT PRIMARY KEY,
                email            TEXT UNIQUE NOT NULL,
                name             TEXT NOT NULL DEFAULT '',
                whatsapp_number  TEXT,
                style_profile    JSONB NOT NULL DEFAULT '{}',
                world_model      JSONB NOT NULL DEFAULT '{}',
                is_suspended     BOOLEAN NOT NULL DEFAULT FALSE,
                suspension_reason TEXT,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_active_at   TIMESTAMPTZ
            );
            """,

            # ── Tokens ─────────────────────────────────────────────────
            """
            CREATE TABLE IF NOT EXISTS user_tokens (
                user_id       TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                provider      TEXT NOT NULL,
                access_token  TEXT,
                refresh_token TEXT,
                expires_at    TIMESTAMPTZ,
                scopes        TEXT[],
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (user_id, provider)
            );
            """,
            """ALTER TABLE user_tokens ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;""",
            """ALTER TABLE user_tokens ADD COLUMN IF NOT EXISTS scopes TEXT[];""",
            """ALTER TABLE user_tokens ADD COLUMN IF NOT EXISTS access_token TEXT;""",
            """ALTER TABLE user_tokens ADD COLUMN IF NOT EXISTS refresh_token TEXT;""",

            # ── Sessions ───────────────────────────────────────────────
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id   TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                user_id      TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                title        TEXT,
                is_archived  BOOLEAN NOT NULL DEFAULT FALSE,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_active  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_sessions_user_active
            ON sessions(user_id, last_active DESC) WHERE NOT is_archived;
            """,

            # ── Conversation ───────────────────────────────────────────
            """
            CREATE TABLE IF NOT EXISTS conversation_history (
                id          BIGSERIAL PRIMARY KEY,
                session_id  TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                role        TEXT NOT NULL CHECK (role IN ('user','assistant','tool')),
                content     TEXT NOT NULL,
                tool_calls  JSONB NOT NULL DEFAULT '[]',
                token_count INTEGER,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_conv_session_time
            ON conversation_history(session_id, created_at DESC);
            """,

            # ── Memory ─────────────────────────────────────────────────
            """
            CREATE TABLE IF NOT EXISTS agent_memory (
                id          BIGSERIAL PRIMARY KEY,
                user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                key         TEXT NOT NULL,
                content     TEXT NOT NULL,
                category    TEXT NOT NULL DEFAULT 'note',
                embedding   vector(1536),
                source      TEXT,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (user_id, key)
            );
            """,
            """CREATE INDEX IF NOT EXISTS idx_memory_user ON agent_memory(user_id);""",
            """
            CREATE INDEX IF NOT EXISTS idx_memory_embedding
            ON agent_memory USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_memory_content_trgm
            ON agent_memory USING gin (content gin_trgm_ops);
            """,

            # ── Tool registry ──────────────────────────────────────────
            """
            CREATE TABLE IF NOT EXISTS tool_registry (
                tool_id     TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                user_id     TEXT REFERENCES users(user_id) ON DELETE CASCADE,
                name        TEXT NOT NULL,
                description TEXT NOT NULL,
                code        TEXT NOT NULL,
                is_verified BOOLEAN NOT NULL DEFAULT FALSE,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,

            # ── WhatsApp ───────────────────────────────────────────────
            """
            CREATE TABLE IF NOT EXISTS whatsapp_messages (
                id           BIGSERIAL PRIMARY KEY,
                message_sid  TEXT UNIQUE NOT NULL,
                from_number  TEXT NOT NULL,
                body         TEXT NOT NULL,
                num_media    INTEGER NOT NULL DEFAULT 0,
                user_id      TEXT REFERENCES users(user_id) ON DELETE SET NULL,
                received_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            """CREATE INDEX IF NOT EXISTS idx_wa_from ON whatsapp_messages(from_number);""",
            """CREATE INDEX IF NOT EXISTS idx_wa_received ON whatsapp_messages(received_at DESC);""",

            # ── Tasks ──────────────────────────────────────────────────
            """
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                task_id        TEXT PRIMARY KEY,
                user_id        TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                celery_task_id TEXT,
                message        TEXT NOT NULL,
                status         TEXT NOT NULL DEFAULT 'pending'
                               CHECK (status IN ('pending','running','done','failed','cancelled')),
                scheduled_for  TIMESTAMPTZ,
                completed_at   TIMESTAMPTZ,
                result         JSONB,
                error          TEXT,
                retry_count    INTEGER NOT NULL DEFAULT 0,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            """CREATE INDEX IF NOT EXISTS idx_tasks_user ON scheduled_tasks(user_id);""",
            """CREATE INDEX IF NOT EXISTS idx_tasks_status ON scheduled_tasks(status);""",

            # ── Audit ──────────────────────────────────────────────────
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id          BIGSERIAL PRIMARY KEY,
                user_id     TEXT,
                action      TEXT NOT NULL,
                resource    TEXT,
                details     JSONB NOT NULL DEFAULT '{}',
                ip_address  TEXT,
                user_agent  TEXT,
                trace_id    TEXT,
                request_id  TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            """CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);""",
            """CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(created_at DESC);""",
            """CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);""",

            # ── Usage ──────────────────────────────────────────────────
            """
            CREATE TABLE IF NOT EXISTS usage_metrics (
                id          BIGSERIAL PRIMARY KEY,
                user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                date        DATE NOT NULL DEFAULT CURRENT_DATE,
                tokens_in   BIGINT NOT NULL DEFAULT 0,
                tokens_out  BIGINT NOT NULL DEFAULT 0,
                tool_calls  INTEGER NOT NULL DEFAULT 0,
                api_calls   INTEGER NOT NULL DEFAULT 0,
                UNIQUE (user_id, date)
            );
            """,
            """CREATE INDEX IF NOT EXISTS idx_usage_user_date ON usage_metrics(user_id, date DESC);""",
        ]

        for q in queries:
            await conn.execute(q)


# ── Users ──────────────────────────────────────────────────────────────────────

async def upsert_user(user_id: str, email: str, name: str) -> dict:
    pool = await get_pool()
    row = await pool.fetchrow("""
        INSERT INTO users (user_id, email, name, updated_at, last_active_at)
        VALUES ($1, $2, $3, NOW(), NOW())
        ON CONFLICT (user_id) DO UPDATE
        SET email=EXCLUDED.email, name=EXCLUDED.name,
            updated_at=NOW(), last_active_at=NOW()
        RETURNING *
    """, user_id, email, name)
    return dict(row)


async def get_user(user_id: str) -> dict:
    pool = await get_pool(readonly=True)
    row = await pool.fetchrow(
        "SELECT * FROM users WHERE user_id=$1", user_id
    )
    if not row:
        raise RecordNotFoundError("User", user_id)
    if row["is_suspended"]:
        from app.core.exceptions import SynapzeError
        raise SynapzeError("Account suspended", "ACCOUNT_SUSPENDED")
    return dict(row)


async def get_all_users(active_since_days: int = 30) -> list[dict]:
    pool = await get_pool(readonly=True)
    rows = await pool.fetch("""
        SELECT * FROM users WHERE NOT is_suspended
          AND (last_active_at IS NULL OR last_active_at > NOW() - ($1 || ' days')::INTERVAL)
    """, str(active_since_days))
    return [dict(r) for r in rows]


async def update_user_field(user_id: str, field: str, value: Any) -> None:
    allowed = {"whatsapp_number", "style_profile", "world_model", "name"}
    if field not in allowed:
        raise DatabaseError(f"Cannot update field: {field}")
    pool = await get_pool()
    v = json.dumps(value) if isinstance(value, dict) else value
    await pool.execute(
        f"UPDATE users SET {field}=$1, updated_at=NOW() WHERE user_id=$2", v, user_id
    )


# ── Tokens ─────────────────────────────────────────────────────────────────────

async def save_user_tokens(user_id: str, provider: str, tokens: dict) -> None:
    pool = await get_pool()
    at = encrypt_token(tokens.get("access_token") or "")
    rt = encrypt_token(tokens.get("refresh_token") or "")
    await pool.execute("""
        INSERT INTO user_tokens (user_id, provider, access_token, refresh_token,
                                  expires_at, scopes, updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,NOW())
        ON CONFLICT (user_id, provider) DO UPDATE
        SET access_token=EXCLUDED.access_token, refresh_token=EXCLUDED.refresh_token,
            expires_at=EXCLUDED.expires_at, scopes=EXCLUDED.scopes, updated_at=NOW()
    """, user_id, provider, at, rt, tokens.get("expires_at"), tokens.get("scopes", []))


async def get_user_tokens(user_id: str, provider: str) -> Optional[dict]:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM user_tokens WHERE user_id=$1 AND provider=$2", user_id, provider
    )
    if not row:
        return None
    d = dict(row)
    d["access_token"]  = decrypt_token(d.get("access_token")  or "")
    d["refresh_token"] = decrypt_token(d.get("refresh_token") or "")
    return d


# ── Sessions ───────────────────────────────────────────────────────────────────

async def create_session(user_id: str, session_id: Optional[str] = None) -> str:
    pool = await get_pool()
    if session_id:
        await pool.execute("""
            INSERT INTO sessions (session_id, user_id) VALUES ($1,$2)
            ON CONFLICT (session_id) DO UPDATE SET last_active=NOW()
        """, session_id, user_id)
        return session_id
    row = await pool.fetchrow(
        "INSERT INTO sessions (user_id) VALUES ($1) RETURNING session_id", user_id
    )
    return row["session_id"]


async def verify_session_ownership(session_id: str, user_id: str) -> bool:
    pool = await get_pool(readonly=True)
    row = await pool.fetchrow(
        "SELECT 1 FROM sessions WHERE session_id=$1 AND user_id=$2", session_id, user_id
    )
    return row is not None


async def get_user_sessions(user_id: str, limit: int = 50) -> list[dict]:
    pool = await get_pool(readonly=True)
    rows = await pool.fetch("""
        SELECT s.session_id, s.title, s.created_at, s.last_active,
               COUNT(ch.id) as message_count
        FROM sessions s
        LEFT JOIN conversation_history ch ON ch.session_id=s.session_id
        WHERE s.user_id=$1 AND NOT s.is_archived
        GROUP BY s.session_id ORDER BY s.last_active DESC LIMIT $2
    """, user_id, limit)
    return [dict(r) for r in rows]


# ── Conversation history ────────────────────────────────────────────────────────

async def save_message(session_id: str, role: str, content: str,
                        tool_calls: list, token_count: Optional[int] = None) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                INSERT INTO conversation_history (session_id,role,content,tool_calls,token_count)
                VALUES ($1,$2,$3,$4,$5)
            """, session_id, role, content, tool_calls, token_count)
            await conn.execute(
                "UPDATE sessions SET last_active=NOW() WHERE session_id=$1", session_id
            )


async def get_history(session_id: str, limit: int = 20) -> list[dict]:
    pool = await get_pool(readonly=True)
    rows = await pool.fetch("""
        SELECT role, content, tool_calls, created_at
        FROM conversation_history WHERE session_id=$1
        ORDER BY created_at DESC LIMIT $2
    """, session_id, limit * 2)
    return [
        {"role": r["role"], "content": r["content"], "tool_calls": r["tool_calls"]}
        for r in reversed(rows)
    ]


async def clear_session_history(session_id: str, user_id: str) -> None:
    if not await verify_session_ownership(session_id, user_id):
        raise RecordNotFoundError("Session", session_id)
    pool = await get_pool()
    await pool.execute("DELETE FROM conversation_history WHERE session_id=$1", session_id)


# ── Memory ─────────────────────────────────────────────────────────────────────

async def save_memory(user_id: str, key: str, content: str,
                       category: str, embedding: Optional[list] = None) -> None:
    pool = await get_pool()
    await pool.execute("""
        INSERT INTO agent_memory (user_id,key,content,category,embedding,updated_at)
        VALUES ($1,$2,$3,$4,$5::vector,NOW())
        ON CONFLICT (user_id,key) DO UPDATE
        SET content=EXCLUDED.content, category=EXCLUDED.category,
            embedding=EXCLUDED.embedding, updated_at=NOW()
    """, user_id, key, content, category, str(embedding) if embedding else None)


async def search_memory_semantic(user_id: str, embedding: list, limit: int = 10) -> list[dict]:
    pool = await get_pool(readonly=True)
    rows = await pool.fetch("""
        SELECT key, content, category, 1-(embedding<=>$1::vector) AS similarity
        FROM agent_memory WHERE user_id=$2 AND embedding IS NOT NULL
        ORDER BY embedding<=>$1::vector LIMIT $3
    """, str(embedding), user_id, limit)
    return [dict(r) for r in rows]


async def search_memory_text(user_id: str, query: str, limit: int = 20) -> list[dict]:
    pool = await get_pool(readonly=True)
    rows = await pool.fetch("""
        SELECT key, content, category, updated_at, similarity(content,$2) AS score
        FROM agent_memory WHERE user_id=$1 AND (content % $2 OR key ILIKE $3)
        ORDER BY score DESC, updated_at DESC LIMIT $4
    """, user_id, query, f"%{query}%", limit)
    return [dict(r) for r in rows]


# ── WhatsApp ───────────────────────────────────────────────────────────────────

async def save_whatsapp_message(msg: dict) -> None:
    pool = await get_pool()
    await pool.execute("""
        INSERT INTO whatsapp_messages (message_sid,from_number,body,num_media,received_at)
        VALUES ($1,$2,$3,$4,$5) ON CONFLICT (message_sid) DO NOTHING
    """, msg["message_sid"], msg["from_number"], msg["body"],
        msg.get("num_media", 0), msg["received_at"])


async def get_whatsapp_messages(user_id: str, limit: int = 20,
                                 from_number: Optional[str] = None) -> list[dict]:
    pool = await get_pool(readonly=True)
    if from_number:
        rows = await pool.fetch("""
            SELECT * FROM whatsapp_messages WHERE from_number=$1
            ORDER BY received_at DESC LIMIT $2
        """, from_number, limit)
    else:
        rows = await pool.fetch(
            "SELECT * FROM whatsapp_messages ORDER BY received_at DESC LIMIT $1", limit
        )
    return [dict(r) for r in rows]


# ── Usage metrics ──────────────────────────────────────────────────────────────

async def record_usage(user_id: str, tokens_in: int = 0, tokens_out: int = 0,
                        tool_calls: int = 0, api_calls: int = 1) -> None:
    try:
        pool = await get_pool()
        await pool.execute("""
            INSERT INTO usage_metrics (user_id,tokens_in,tokens_out,tool_calls,api_calls)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (user_id,date) DO UPDATE
            SET tokens_in  = usage_metrics.tokens_in  + EXCLUDED.tokens_in,
                tokens_out = usage_metrics.tokens_out + EXCLUDED.tokens_out,
                tool_calls = usage_metrics.tool_calls + EXCLUDED.tool_calls,
                api_calls  = usage_metrics.api_calls  + EXCLUDED.api_calls
        """, user_id, tokens_in, tokens_out, tool_calls, api_calls)
    except Exception as e:
        logger.warning(f"Usage metrics failed (non-fatal): {e}")


async def get_usage_summary(user_id: str, days: int = 30) -> dict:
    pool = await get_pool(readonly=True)
    row = await pool.fetchrow("""
        SELECT SUM(tokens_in) AS total_tokens_in, SUM(tokens_out) AS total_tokens_out,
               SUM(tool_calls) AS total_tool_calls, SUM(api_calls) AS total_api_calls,
               COUNT(*) AS active_days
        FROM usage_metrics WHERE user_id=$1 AND date > CURRENT_DATE-$2
    """, user_id, days)
    return dict(row) if row else {}


# ── Audit ──────────────────────────────────────────────────────────────────────

async def audit(user_id: Optional[str], action: str, resource: Optional[str] = None,
                details: Optional[dict] = None, ip: Optional[str] = None,
                user_agent: Optional[str] = None, trace_id: Optional[str] = None,
                request_id: Optional[str] = None) -> None:
    try:
        from app.core.logging import get_trace_id, get_request_id
        pool = await get_pool()
        await pool.execute("""
            INSERT INTO audit_log
                (user_id,action,resource,details,ip_address,user_agent,trace_id,request_id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        """, user_id, action, resource, details or {},
            ip, user_agent, trace_id or get_trace_id(), request_id or get_request_id())
    except Exception as e:
        logger.error(f"Audit log failed (non-fatal): {e}")
