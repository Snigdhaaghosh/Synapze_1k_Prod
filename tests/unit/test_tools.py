"""Unit tests — Tool layer"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestGmailTool:
    @pytest.fixture
    def gmail(self):
        with patch("app.tools.gmail.GmailTool._check_config"):
            from app.tools.gmail import GmailTool
            tool = GmailTool.__new__(GmailTool)
            tool.user_id = "test-user"
            tool.logger = MagicMock()
            tool._service = None
            return tool

    @pytest.mark.asyncio
    async def test_send_email_validates_recipient(self, gmail):
        gmail._get_service = AsyncMock(return_value=MagicMock())
        result = await gmail.send_email(to="notanemail", subject="test", body="hi")
        assert "error" in result
        assert "Invalid" in result["error"]

    @pytest.mark.asyncio
    async def test_send_email_requires_subject(self, gmail):
        gmail._get_service = AsyncMock(return_value=MagicMock())
        result = await gmail.send_email(to="valid@example.com", subject="", body="hi")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_send_email_requires_body(self, gmail):
        gmail._get_service = AsyncMock(return_value=MagicMock())
        result = await gmail.send_email(to="valid@example.com", subject="Subject", body="")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_read_email_requires_id(self, gmail):
        result = await gmail.read_email(email_id="")
        assert "error" in result

    def test_extract_body_from_plain_text(self, gmail):
        import base64
        text = "Hello, this is the email body"
        encoded = base64.urlsafe_b64encode(text.encode()).decode()
        payload = {
            "parts": [{"mimeType": "text/plain", "body": {"data": encoded}}]
        }
        result = gmail._extract_body(payload)
        assert result == text

    def test_extract_body_falls_back_to_html(self, gmail):
        import base64
        html = "<p>Hello world</p>"
        encoded = base64.urlsafe_b64encode(html.encode()).decode()
        payload = {
            "parts": [{"mimeType": "text/html", "body": {"data": encoded}}]
        }
        result = gmail._extract_body(payload)
        assert "Hello world" in result


class TestCalendarTool:
    @pytest.fixture
    def cal(self):
        with patch("app.tools.integrations.CalendarTool._check_config"):
            from app.tools.integrations import CalendarTool
            tool = CalendarTool.__new__(CalendarTool)
            tool.user_id = "test-user"
            tool.logger = MagicMock()
            tool._service = None
            return tool

    @pytest.mark.asyncio
    async def test_create_event_requires_all_fields(self, cal):
        cal._get_service = AsyncMock(return_value=MagicMock())
        result = await cal.create_event(title="", start_datetime="", end_datetime="")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_check_availability_detects_conflicts(self, cal):
        mock_service = MagicMock()
        mock_service.freebusy().query().execute.return_value = {
            "calendars": {"primary": {"busy": [{"start": "2025-01-01T14:00:00Z"}]}}
        }
        cal._service = mock_service
        cal._get_service = AsyncMock(return_value=mock_service)
        result = await cal.check_availability("2025-01-01T14:00:00Z", "2025-01-01T15:00:00Z")
        assert result["has_conflict"] is True

    @pytest.mark.asyncio
    async def test_delete_event_requires_id(self, cal):
        result = await cal.delete_event(event_id="")
        assert "error" in result


class TestCircuitBreaker:
    def test_circuit_closed_initially(self):
        from app.agent.registry import CircuitBreaker
        cb = CircuitBreaker()
        assert cb.is_open("gmail_send_email") is False

    def test_circuit_opens_after_failures(self):
        from app.agent.registry import CircuitBreaker
        cb = CircuitBreaker()
        for _ in range(cb.FAILURE_THRESHOLD):
            cb.record_failure("some_tool")
        assert cb.is_open("some_tool") is True

    def test_circuit_resets_after_success(self):
        from app.agent.registry import CircuitBreaker
        cb = CircuitBreaker()
        for _ in range(cb.FAILURE_THRESHOLD):
            cb.record_failure("some_tool")
        cb.record_success("some_tool")
        assert cb.is_open("some_tool") is False

    def test_different_tools_independent(self):
        from app.agent.registry import CircuitBreaker
        cb = CircuitBreaker()
        for _ in range(cb.FAILURE_THRESHOLD):
            cb.record_failure("tool_a")
        assert cb.is_open("tool_a") is True
        assert cb.is_open("tool_b") is False


class TestBrowserAgent:
    def test_blocks_localhost_url(self):
        from app.agent.browser_agent import _is_url_blocked
        assert _is_url_blocked("http://localhost:8000/admin") is True
        assert _is_url_blocked("http://127.0.0.1/secret") is True

    def test_blocks_internal_network(self):
        from app.agent.browser_agent import _is_url_blocked
        assert _is_url_blocked("http://192.168.1.1/") is True
        assert _is_url_blocked("http://10.0.0.1/") is True

    def test_blocks_aws_metadata(self):
        from app.agent.browser_agent import _is_url_blocked
        assert _is_url_blocked("http://169.254.169.254/latest/meta-data/") is True

    def test_allows_public_urls(self):
        from app.agent.browser_agent import _is_url_blocked
        assert _is_url_blocked("https://google.com") is False
        assert _is_url_blocked("https://github.com") is False
        assert _is_url_blocked("https://api.openai.com") is False

    def test_blocks_file_scheme(self):
        from app.agent.browser_agent import _is_url_blocked
        assert _is_url_blocked("file:///etc/passwd") is True
