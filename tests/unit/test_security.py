"""Unit tests — Security middleware + sanitization"""
import pytest
from app.core.security import sanitize_input, hash_token, mask_email


class TestSanitizeInput:
    def test_normal_text_unchanged(self):
        result = sanitize_input("Hello, world!")
        assert result == "Hello, world!"

    def test_strips_null_bytes(self):
        result = sanitize_input("Hello\x00World")
        assert "\x00" not in result
        assert "Hello" in result

    def test_truncates_to_max_length(self):
        long_text = "a" * 20_000
        result = sanitize_input(long_text, max_length=100)
        assert len(result) == 100

    def test_preserves_newlines(self):
        text = "Line 1\nLine 2\nLine 3"
        result = sanitize_input(text)
        assert "\n" in result

    def test_strips_whitespace_edges(self):
        result = sanitize_input("   hello   ")
        assert result == "hello"

    def test_empty_string_returns_empty(self):
        assert sanitize_input("") == ""
        assert sanitize_input("   ") == ""

    def test_unicode_preserved(self):
        text = "नमस्ते दुनिया"  # Hindi
        result = sanitize_input(text)
        assert "नमस्ते" in result


class TestHashToken:
    def test_returns_fixed_length_string(self):
        result = hash_token("some-token-value")
        assert len(result) == 16

    def test_same_input_same_output(self):
        assert hash_token("abc") == hash_token("abc")

    def test_different_inputs_different_outputs(self):
        assert hash_token("token1") != hash_token("token2")


class TestMaskEmail:
    def test_masks_local_part(self):
        result = mask_email("john@example.com")
        assert result == "j***@example.com"

    def test_preserves_domain(self):
        result = mask_email("user@company.org")
        assert "company.org" in result

    def test_handles_invalid_email(self):
        result = mask_email("notanemail")
        assert result == "***"
