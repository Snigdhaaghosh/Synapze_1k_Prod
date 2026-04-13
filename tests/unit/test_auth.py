"""Unit tests — JWT authentication"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

from app.auth.jwt import (
    create_access_token, create_refresh_token, decode_token,
    is_token_revoked, revoke_token,
)
from app.config import settings
from app.core.exceptions import AuthError, TokenExpiredError


class TestCreateAccessToken:
    def test_creates_valid_token(self):
        token = create_access_token("user123", "test@example.com")
        assert isinstance(token, str)
        assert len(token) > 50

    def test_token_has_correct_type(self):
        token = create_access_token("user123", "test@example.com")
        payload = decode_token(token, "access")
        assert payload["type"] == "access"
        assert payload["sub"] == "user123"
        assert payload["email"] == "test@example.com"

    def test_token_has_jti(self):
        token = create_access_token("user123", "test@example.com")
        payload = decode_token(token, "access")
        assert "jti" in payload
        assert len(payload["jti"]) > 10

    def test_two_tokens_have_different_jtis(self):
        t1 = create_access_token("user123", "test@example.com")
        t2 = create_access_token("user123", "test@example.com")
        p1 = decode_token(t1, "access")
        p2 = decode_token(t2, "access")
        assert p1["jti"] != p2["jti"]


class TestCreateRefreshToken:
    def test_creates_valid_refresh_token(self):
        token = create_refresh_token("user123", "test@example.com")
        payload = decode_token(token, "refresh")
        assert payload["type"] == "refresh"
        assert payload["sub"] == "user123"

    def test_refresh_token_not_valid_as_access(self):
        token = create_refresh_token("user123", "test@example.com")
        with pytest.raises(AuthError):
            decode_token(token, "access")


class TestDecodeToken:
    def test_decodes_valid_token(self):
        token = create_access_token("user456", "user@example.com")
        payload = decode_token(token)
        assert payload["sub"] == "user456"

    def test_rejects_tampered_token(self):
        token = create_access_token("user456", "user@example.com")
        tampered = token[:-5] + "XXXXX"
        with pytest.raises(AuthError):
            decode_token(tampered)

    def test_rejects_wrong_type(self):
        refresh_token = create_refresh_token("user456", "user@example.com")
        with pytest.raises(AuthError, match="Invalid token type"):
            decode_token(refresh_token, "access")


class TestTokenRevocation:
    @pytest.mark.asyncio
    async def test_new_token_not_revoked(self):
        with patch("app.auth.jwt._get_redis") as mock_get:
            r = AsyncMock()
            r.exists = AsyncMock(return_value=0)
            mock_get.return_value = r
            result = await is_token_revoked("some-jti-value")
            assert result is False

    @pytest.mark.asyncio
    async def test_revoked_token_detected(self):
        with patch("app.auth.jwt._get_redis") as mock_get:
            r = AsyncMock()
            r.exists = AsyncMock(return_value=1)
            mock_get.return_value = r
            result = await is_token_revoked("revoked-jti")
            assert result is True

    @pytest.mark.asyncio
    async def test_revoke_token_calls_setex(self):
        with patch("app.auth.jwt._get_redis") as mock_get:
            r = AsyncMock()
            r.setex = AsyncMock(return_value=True)
            mock_get.return_value = r
            await revoke_token("jti-to-revoke", expire_seconds=3600)
            r.setex.assert_called_once()
            call_args = r.setex.call_args[0]
            assert "jti-to-revoke" in call_args[0]

    @pytest.mark.asyncio
    async def test_redis_failure_does_not_crash(self):
        """If Redis is down, is_token_revoked should fail open (allow)."""
        with patch("app.auth.jwt._get_redis") as mock_get:
            r = AsyncMock()
            r.exists = AsyncMock(side_effect=Exception("Redis down"))
            mock_get.return_value = r
            result = await is_token_revoked("any-jti")
            assert result is False  # fail open
