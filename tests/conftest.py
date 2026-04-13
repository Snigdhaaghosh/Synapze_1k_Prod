"""
Synapze Enterprise — Test configuration and shared fixtures.
"""
import asyncio
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock, patch


# ── Event loop ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── App fixture ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def app():
    """Create the FastAPI app with test settings."""
    import os
    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault("JWT_SECRET", "test-secret-key-that-is-at-least-64-characters-long-for-testing")
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
    os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
    os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-secret")
    os.environ.setdefault("DATABASE_URL", "postgresql://synapze_test:testpassword@localhost:5432/synapze_test")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

    from app.main import app
    return app


@pytest_asyncio.fixture
async def client(app):
    """AsyncClient for testing endpoints."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ── Auth fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def test_user_id():
    return "test-user-12345"


@pytest.fixture
def test_user_email():
    return "test@example.com"


@pytest.fixture
def auth_token(test_user_id, test_user_email):
    from app.auth.jwt import create_access_token
    return create_access_token(user_id=test_user_id, email=test_user_email)


@pytest.fixture
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


# ── DB mocks ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_db():
    """Mock all database calls."""
    with patch("app.db.database.get_pool") as mock_pool:
        pool = AsyncMock()
        mock_pool.return_value = pool
        yield pool


@pytest.fixture
def mock_user(test_user_id, test_user_email):
    return {
        "user_id": test_user_id,
        "email": test_user_email,
        "name": "Test User",
        "whatsapp_number": None,
        "style_profile": {},
        "world_model": {},
        "is_suspended": False,
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
    }


# ── Anthropic mock ────────────────────────────────────────────────────────────

@pytest.fixture
def mock_anthropic_response():
    """Standard mock Anthropic response."""
    content_block = MagicMock()
    content_block.type = "text"
    content_block.text = "Test response from agent"

    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [content_block]
    response.usage.input_tokens = 100
    response.usage.output_tokens = 50
    return response
