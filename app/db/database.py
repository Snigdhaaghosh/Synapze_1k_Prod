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