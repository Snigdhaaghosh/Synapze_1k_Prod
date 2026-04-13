"""
Synapze V2 — Base tool class
Every tool inherits this. Timeout, error wrapping, and logging guaranteed.
"""
import asyncio
import functools
from abc import ABC, abstractmethod
from typing import Any, Optional

from app.config import settings
from app.core.exceptions import (
    AgentTimeoutError,
    IntegrationNotConfiguredError,
    ToolError,
)
from app.core.logging import get_logger


class BaseTool(ABC):
    """
    All tools inherit this base class.

    Guarantees:
    - Every call has a timeout (AGENT_TOOL_TIMEOUT_SECS)
    - Every exception is caught and wrapped as ToolError
    - Every call is logged with duration
    - Integration config is checked before first use
    """

    tool_name: str = "base_tool"
    required_config: list[str] = []   # setting names that must be non-empty

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.logger = get_logger(f"tool.{self.tool_name}")
        self._check_config()

    def _check_config(self):
        """Fail fast if required settings are missing."""
        for key in self.required_config:
            val = getattr(settings, key, "")
            if not val:
                raise IntegrationNotConfiguredError(self.tool_name)

    async def safe_execute(self, method_name: str, **kwargs) -> dict:
        """
        Execute a tool method with timeout + error wrapping.
        Always returns a dict. Never raises (wraps into error dict).
        """
        import time
        start = time.monotonic()
        try:
            method = getattr(self, method_name)
            result = await asyncio.wait_for(
                method(**kwargs),
                timeout=settings.AGENT_TOOL_TIMEOUT_SECS,
            )
            duration = round((time.monotonic() - start) * 1000)
            self.logger.info(f"{method_name} ok", extra={
                "tool": self.tool_name,
                "method": method_name,
                "duration_ms": duration,
            })
            return result if isinstance(result, dict) else {"result": result}

        except asyncio.TimeoutError:
            err = AgentTimeoutError(self.tool_name, settings.AGENT_TOOL_TIMEOUT_SECS)
            self.logger.error(f"{method_name} timeout", extra={"tool": self.tool_name})
            return {"error": err.message, "code": err.code}

        except IntegrationNotConfiguredError as e:
            return {"error": e.message, "code": e.code}

        except ToolError as e:
            self.logger.error(f"{method_name} tool error: {e.message}",
                              extra={"tool": self.tool_name})
            return {"error": e.message, "code": e.code}

        except Exception as e:
            self.logger.error(f"{method_name} unexpected error: {e}",
                              exc_info=True, extra={"tool": self.tool_name})
            return {"error": str(e), "code": "TOOL_ERROR"}
