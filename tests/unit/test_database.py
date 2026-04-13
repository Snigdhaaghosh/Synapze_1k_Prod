"""Unit tests — Database layer"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        with patch("app.db.database.settings") as s:
            s.ENCRYPTION_KEY = "test-encryption-key-for-unit-tests"
            from app.db.database import encrypt_token, decrypt_token
            original = "sk-ant-supersecret-token-value"
            encrypted = encrypt_token(original)
            assert encrypted != original
            decrypted = decrypt_token(encrypted)
            assert decrypted == original

    def test_encrypt_empty_returns_empty(self):
        from app.db.database import encrypt_token
        assert encrypt_token("") == ""

    def test_decrypt_unencrypted_returns_as_is(self):
        """Handles legacy unencrypted values gracefully."""
        from app.db.database import decrypt_token
        plain = "plain-text-token"
        result = decrypt_token(plain)
        assert result == plain

    def test_no_encryption_key_returns_plaintext(self):
        with patch("app.db.database.settings") as s:
            s.ENCRYPTION_KEY = ""
            from app.db.database import encrypt_token
            token = "some-token"
            assert encrypt_token(token) == token


class TestAuditLog:
    @pytest.mark.asyncio
    async def test_audit_never_raises(self):
        """Audit log failure must never propagate."""
        with patch("app.db.database.get_pool", side_effect=Exception("DB down")):
            from app.db.database import audit
            # Should not raise
            await audit("user123", "test.action", details={"key": "value"})

    @pytest.mark.asyncio
    async def test_audit_records_all_fields(self):
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_pool.execute = AsyncMock()
        with patch("app.db.database.get_pool", return_value=mock_pool):
            from app.db.database import audit
            await audit(
                user_id="user123",
                action="test.action",
                resource="session:abc",
                details={"key": "value"},
                ip="1.2.3.4",
            )
            mock_pool.execute.assert_called_once()
            call_args = mock_pool.execute.call_args[0]
            assert "user123" in call_args
            assert "test.action" in call_args


class TestUsageMetrics:
    @pytest.mark.asyncio
    async def test_record_usage_never_raises(self):
        """Usage metrics failure must never propagate."""
        with patch("app.db.database.get_pool", side_effect=Exception("DB down")):
            from app.db.database import record_usage
            await record_usage("user123", tokens_in=100, tokens_out=50)

    @pytest.mark.asyncio
    async def test_record_usage_calls_db(self):
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock()
        with patch("app.db.database.get_pool", return_value=mock_pool):
            from app.db.database import record_usage
            await record_usage("user123", tokens_in=500, tokens_out=200, tool_calls=3)
            mock_pool.execute.assert_called_once()


class TestSanitizeInputEdgeCases:
    def test_very_long_input_truncated(self):
        from app.core.security import sanitize_input
        text = "a" * 50_000
        result = sanitize_input(text, max_length=1000)
        assert len(result) <= 1000

    def test_null_byte_injection_removed(self):
        from app.core.security import sanitize_input
        malicious = "normal text\x00DROP TABLE users;"
        result = sanitize_input(malicious)
        assert "\x00" not in result
        assert "normal text" in result

    def test_unicode_emoji_preserved(self):
        from app.core.security import sanitize_input
        text = "Hello 🌍 World 🚀"
        result = sanitize_input(text)
        assert "🌍" in result
        assert "🚀" in result
