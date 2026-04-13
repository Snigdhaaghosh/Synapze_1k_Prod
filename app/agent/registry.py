"""
Synapze Enterprise — Tool Registry + Executor
Adds circuit breaker pattern per tool:
  - After 3 consecutive failures, tool is open-circuited for 60s
  - Automatically resets after the cooldown window
"""
import time
from collections import defaultdict
from typing import Any

from app.core.exceptions import IntegrationNotConfiguredError, ToolNotFoundError
from app.core.logging import get_logger

logger = get_logger("registry")

# ── Tool definitions (same schema, easier to extend) ──────────────────────────
TOOL_DEFINITIONS = [
    # Gmail
    {"name": "gmail_list_emails",
     "description": "List emails from Gmail inbox. Filter by query, label, unread status.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Gmail search query e.g. 'is:unread from:boss@company.com'"},
         "max_results": {"type": "integer", "default": 10},
         "label": {"type": "string", "default": "INBOX"},
     }}},
    {"name": "gmail_read_email",
     "description": "Read full content of a specific email by ID.",
     "input_schema": {"type": "object", "required": ["email_id"], "properties": {
         "email_id": {"type": "string"},
     }}},
    {"name": "gmail_send_email",
     "description": "Send an email. Always confirm content before sending unless user said auto-mode.",
     "input_schema": {"type": "object", "required": ["to", "subject", "body"], "properties": {
         "to": {"type": "string"},
         "subject": {"type": "string"},
         "body": {"type": "string"},
         "cc": {"type": "string"},
         "reply_to_id": {"type": "string"},
     }}},
    {"name": "gmail_search_emails",
     "description": "Advanced email search with date filters.",
     "input_schema": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string"},
         "date_after": {"type": "string"},
         "date_before": {"type": "string"},
         "max_results": {"type": "integer", "default": 20},
     }}},
    # Calendar
    {"name": "calendar_list_events",
     "description": "List upcoming calendar events.",
     "input_schema": {"type": "object", "properties": {
         "days_ahead": {"type": "integer", "default": 7},
     }}},
    {"name": "calendar_check_availability",
     "description": "Check if a time slot is free. Always run before creating events.",
     "input_schema": {"type": "object", "required": ["start_datetime", "end_datetime"], "properties": {
         "start_datetime": {"type": "string", "description": "ISO 8601 e.g. 2025-06-10T14:00:00+05:30"},
         "end_datetime": {"type": "string"},
     }}},
    {"name": "calendar_create_event",
     "description": "Create a calendar event. Checks conflicts automatically.",
     "input_schema": {"type": "object", "required": ["title", "start_datetime", "end_datetime"], "properties": {
         "title": {"type": "string"},
         "start_datetime": {"type": "string"},
         "end_datetime": {"type": "string"},
         "description": {"type": "string"},
         "attendees": {"type": "array", "items": {"type": "string"}},
         "location": {"type": "string"},
         "add_meet_link": {"type": "boolean", "default": False},
     }}},
    {"name": "calendar_delete_event",
     "description": "Delete a calendar event by ID.",
     "input_schema": {"type": "object", "required": ["event_id"], "properties": {
         "event_id": {"type": "string"},
     }}},
    # WhatsApp
    {"name": "whatsapp_send_message",
     "description": "Send a WhatsApp message. Use E.164 format for number e.g. +919876543210",
     "input_schema": {"type": "object", "required": ["to", "message"], "properties": {
         "to": {"type": "string"},
         "message": {"type": "string"},
         "media_url": {"type": "string"},
     }}},
    {"name": "whatsapp_list_recent",
     "description": "List recent incoming WhatsApp messages.",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer", "default": 20},
         "from_number": {"type": "string"},
     }}},
    # Slack
    {"name": "slack_send_message",
     "description": "Send a Slack message to a channel or DM.",
     "input_schema": {"type": "object", "required": ["channel", "message"], "properties": {
         "channel": {"type": "string"},
         "message": {"type": "string"},
         "thread_ts": {"type": "string"},
     }}},
    {"name": "slack_list_messages",
     "description": "List recent messages from a Slack channel.",
     "input_schema": {"type": "object", "required": ["channel"], "properties": {
         "channel": {"type": "string"},
         "limit": {"type": "integer", "default": 20},
     }}},
    {"name": "slack_list_channels",
     "description": "List all Slack channels the bot has access to.",
     "input_schema": {"type": "object", "properties": {}}},
    # Browser
    {"name": "browser_execute",
     "description": "Navigate any website and perform actions. Use for web apps without APIs.",
     "input_schema": {"type": "object", "required": ["task", "url"], "properties": {
         "task": {"type": "string"},
         "url": {"type": "string"},
         "site_key": {"type": "string"},
     }}},
    # Memory
    {"name": "memory_save",
     "description": "Save something to remember permanently across sessions.",
     "input_schema": {"type": "object", "required": ["key", "content"], "properties": {
         "key": {"type": "string"},
         "content": {"type": "string"},
         "category": {"type": "string", "enum": ["contact", "preference", "task", "note", "instruction", "project"]},
     }}},
    {"name": "memory_search",
     "description": "Search saved memories by keyword or meaning.",
     "input_schema": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string"},
     }}},
]


# ── Circuit Breaker ────────────────────────────────────────────────────────────

class CircuitBreaker:
    """
    Per-tool circuit breaker.
    States: CLOSED (normal) → OPEN (too many failures) → HALF_OPEN (testing)
    """
    FAILURE_THRESHOLD = 3       # open after this many consecutive failures
    RESET_TIMEOUT = 60.0        # seconds before trying again (HALF_OPEN)

    def __init__(self):
        self._failures: dict[str, int] = defaultdict(int)
        self._open_since: dict[str, float] = {}

    def is_open(self, tool_name: str) -> bool:
        if tool_name not in self._open_since:
            return False
        elapsed = time.monotonic() - self._open_since[tool_name]
        if elapsed > self.RESET_TIMEOUT:
            # Move to HALF_OPEN — try once
            del self._open_since[tool_name]
            self._failures[tool_name] = 0
            return False
        return True

    def record_success(self, tool_name: str) -> None:
        self._failures[tool_name] = 0
        self._open_since.pop(tool_name, None)

    def record_failure(self, tool_name: str) -> None:
        self._failures[tool_name] += 1
        if self._failures[tool_name] >= self.FAILURE_THRESHOLD:
            self._open_since[tool_name] = time.monotonic()
            logger.warning(f"Circuit opened for tool: {tool_name}",
                           extra={"tool": tool_name, "failures": self._failures[tool_name]})

    def get_status(self) -> dict:
        return {
            "open_circuits": list(self._open_since.keys()),
            "failure_counts": dict(self._failures),
        }


_circuit_breaker = CircuitBreaker()


# ── Tool Executor ──────────────────────────────────────────────────────────────

class ToolExecutor:
    """Routes tool calls to the correct handler with circuit breaker protection."""

    async def execute(self, tool_name: str, tool_input: dict, user_id: str) -> Any:
        # Circuit breaker check
        if _circuit_breaker.is_open(tool_name):
            logger.warning(f"Circuit open for {tool_name} — skipping call")
            return {
                "error": f"Tool '{tool_name}' is temporarily unavailable (circuit open). Try again in 60 seconds.",
                "code": "CIRCUIT_OPEN",
            }

        logger.info(f"Executing tool: {tool_name}", extra={
            "tool": tool_name, "user_id": user_id[:8] + "...",
        })

        try:
            handler = self._get_handler(tool_name, user_id)
            method, kwargs = self._resolve_method(tool_name, handler, tool_input, user_id)
            result = await method(**kwargs)
            _circuit_breaker.record_success(tool_name)
            return result

        except IntegrationNotConfiguredError as e:
            # Not a transient error — don't count against circuit
            return {"error": e.message, "code": e.code,
                    "hint": f"Run /auth {tool_name.split('_')[0]} to configure"}
        except ToolNotFoundError as e:
            return {"error": e.message, "code": e.code}
        except Exception as e:
            _circuit_breaker.record_failure(tool_name)
            logger.error(f"Tool {tool_name} failed: {e}", exc_info=True)
            return {"error": str(e), "code": "TOOL_ERROR", "tool": tool_name}

    def _get_handler(self, tool_name: str, user_id: str):
        from app.tools.gmail import GmailTool
        from app.tools.integrations import CalendarTool, WhatsAppTool, SlackTool
        from app.agent.browser_agent import BrowserAgent
        from app.agent.memory import MemoryManager

        prefix = tool_name.split("_")[0]
        dispatch = {
            "gmail": lambda: GmailTool(user_id=user_id),
            "calendar": lambda: CalendarTool(user_id=user_id),
            "whatsapp": lambda: WhatsAppTool(user_id=user_id),
            "slack": lambda: SlackTool(user_id=user_id),
            "browser": lambda: BrowserAgent(user_id=user_id),
            "memory": lambda: MemoryManager(),
        }
        if prefix not in dispatch:
            raise ToolNotFoundError(tool_name)
        return dispatch[prefix]()

    def _resolve_method(self, tool_name: str, handler, tool_input: dict, user_id: str):
        METHOD_MAP = {
            "gmail_list_emails":          ("list_emails",           tool_input),
            "gmail_read_email":           ("read_email",            tool_input),
            "gmail_send_email":           ("send_email",            tool_input),
            "gmail_search_emails":        ("search_emails",         tool_input),
            "calendar_list_events":       ("list_events",           tool_input),
            "calendar_check_availability":("check_availability",    tool_input),
            "calendar_create_event":      ("create_event",          tool_input),
            "calendar_delete_event":      ("delete_event",          tool_input),
            "whatsapp_send_message":      ("send_message",          tool_input),
            "whatsapp_list_recent":       ("list_recent",           tool_input),
            "slack_send_message":         ("send_message",          tool_input),
            "slack_list_messages":        ("list_messages",         tool_input),
            "slack_list_channels":        ("list_channels",         {}),
            "browser_execute":            ("execute",               tool_input),
            "memory_save":                ("save_memory",           {"user_id": user_id, **tool_input}),
            "memory_search":              ("search",                {"user_id": user_id, **tool_input}),
        }
        if tool_name not in METHOD_MAP:
            raise ToolNotFoundError(tool_name)
        method_name, kwargs = METHOD_MAP[tool_name]
        return getattr(handler, method_name), kwargs
