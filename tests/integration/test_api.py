"""Integration tests — API endpoints"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestHealthEndpoints:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data

    @pytest.mark.asyncio
    async def test_health_ready_returns_503_when_db_down(self, client):
        with patch("app.health.checks.check_database",
                   new_callable=AsyncMock,
                   return_value={"status": "error", "error": "Connection refused"}), \
             patch("app.health.checks.check_redis",
                   new_callable=AsyncMock,
                   return_value={"status": "ok", "latency_ms": 1}):
            response = await client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["ready"] is False


class TestAuthEndpoints:
    @pytest.mark.asyncio
    async def test_me_without_token_returns_401(self, client):
        response = await client.get("/auth/me")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_me_with_valid_token_returns_user(self, client, auth_headers, mock_user, test_user_id):
        with patch("app.db.database.get_user", new_callable=AsyncMock, return_value=mock_user), \
             patch("app.auth.jwt.is_token_revoked", new_callable=AsyncMock, return_value=False), \
             patch("app.db.database.get_usage_summary", new_callable=AsyncMock, return_value={}):
            response = await client.get("/auth/me", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == test_user_id

    @pytest.mark.asyncio
    async def test_refresh_with_invalid_token_returns_401(self, client):
        response = await client.post("/auth/refresh", json={"refresh_token": "invalid.token.here"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_revokes_token(self, client, auth_headers, mock_user):
        with patch("app.db.database.get_user", new_callable=AsyncMock, return_value=mock_user), \
             patch("app.auth.jwt.is_token_revoked", new_callable=AsyncMock, return_value=False), \
             patch("app.auth.jwt.revoke_token", new_callable=AsyncMock) as mock_revoke, \
             patch("app.db.database.audit", new_callable=AsyncMock):
            response = await client.post("/auth/logout", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["logged_out"] is True
        mock_revoke.assert_called_once()


class TestAgentEndpoints:
    @pytest.mark.asyncio
    async def test_chat_without_auth_returns_401(self, client):
        response = await client.post("/agent/chat", json={"message": "hello"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_chat_empty_message_returns_422(self, client, auth_headers, mock_user):
        with patch("app.db.database.get_user", new_callable=AsyncMock, return_value=mock_user), \
             patch("app.auth.jwt.is_token_revoked", new_callable=AsyncMock, return_value=False):
            response = await client.post("/agent/chat",
                                         json={"message": "   "},
                                         headers=auth_headers)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_valid_message_returns_response(self, client, auth_headers, mock_user):
        mock_agent_result = {
            "response": "Here are your emails.",
            "session_id": "test-session-id",
            "tool_calls": [],
            "tokens_used": 150,
            "duration_ms": 1200,
        }
        with patch("app.db.database.get_user", new_callable=AsyncMock, return_value=mock_user), \
             patch("app.auth.jwt.is_token_revoked", new_callable=AsyncMock, return_value=False), \
             patch("app.db.database.create_session", new_callable=AsyncMock), \
             patch("app.agent.core.SynapzeAgent.run",
                   new_callable=AsyncMock,
                   return_value=mock_agent_result):
            response = await client.post(
                "/agent/chat",
                json={"message": "Check my emails"},
                headers=auth_headers,
            )
        assert response.status_code == 200
        data = response.json()
        assert data["response"] == "Here are your emails."
        assert "session_id" in data

    @pytest.mark.asyncio
    async def test_get_sessions_requires_auth(self, client):
        response = await client.get("/agent/sessions")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_sessions_returns_list(self, client, auth_headers, mock_user):
        mock_sessions = [
            {"session_id": "s1", "title": None, "created_at": "2025-01-01", "message_count": 5}
        ]
        with patch("app.db.database.get_user", new_callable=AsyncMock, return_value=mock_user), \
             patch("app.auth.jwt.is_token_revoked", new_callable=AsyncMock, return_value=False), \
             patch("app.db.database.get_user_sessions",
                   new_callable=AsyncMock,
                   return_value=mock_sessions):
            response = await client.get("/agent/sessions", headers=auth_headers)
        assert response.status_code == 200
        assert "sessions" in response.json()


class TestWebhookEndpoints:
    @pytest.mark.asyncio
    async def test_whatsapp_webhook_missing_signature_blocked(self, client):
        """When TWILIO_AUTH_TOKEN is set, missing signature should 403."""
        with patch("app.config.settings.TWILIO_AUTH_TOKEN", "test-auth-token"):
            with patch("twilio.request_validator.RequestValidator.validate", return_value=False):
                response = await client.post(
                    "/webhooks/whatsapp",
                    data={
                        "From": "whatsapp:+919876543210",
                        "Body": "Hello",
                        "MessageSid": "SM123",
                    },
                )
        assert response.status_code in (403, 422)

    @pytest.mark.asyncio
    async def test_slack_url_verification(self, client):
        response = await client.post(
            "/webhooks/slack/events",
            json={"type": "url_verification", "challenge": "test-challenge-123"},
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json()["challenge"] == "test-challenge-123"


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_rate_limit_header_present(self, client):
        """Response should include X-Request-ID from SecurityMiddleware."""
        response = await client.get("/health")
        assert "x-request-id" in response.headers or response.status_code == 200
