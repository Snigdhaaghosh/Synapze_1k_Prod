"""
Synapze Enterprise — Typed exception hierarchy.
Never raise generic Exception. Always raise a typed SynapzeError.
"""
from typing import Optional


class SynapzeError(Exception):
    def __init__(self, message: str, code: str = "SYNAPZE_ERROR",
                 details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.message, "details": self.details}


# Auth
class AuthError(SynapzeError):
    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, "AUTH_ERROR")

class TokenExpiredError(AuthError):
    def __init__(self):
        super().__init__("Token expired")

class InsufficientPermissionsError(AuthError):
    def __init__(self, resource: str):
        super().__init__(f"No permission to access: {resource}", )
        self.code = "INSUFFICIENT_PERMISSIONS"

# Agent
class AgentError(SynapzeError):
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, "AGENT_ERROR", details)

class AgentLoopLimitError(AgentError):
    def __init__(self, iterations: int):
        super().__init__(f"Agent exceeded max iterations ({iterations})", {"iterations": iterations})

class AgentTimeoutError(AgentError):
    def __init__(self, tool: str, timeout: int):
        super().__init__(f"Tool '{tool}' timed out after {timeout}s", {"tool": tool, "timeout_secs": timeout})

# Tool
class ToolError(SynapzeError):
    def __init__(self, tool: str, message: str, details: Optional[dict] = None):
        super().__init__(message, "TOOL_ERROR", {"tool": tool, **(details or {})})
        self.tool = tool

class ToolNotFoundError(ToolError):
    def __init__(self, tool: str):
        super().__init__(tool, f"Tool not registered: '{tool}'")
        self.code = "TOOL_NOT_FOUND"

class IntegrationNotConfiguredError(ToolError):
    def __init__(self, integration: str):
        super().__init__(integration, f"{integration} not configured. Run /auth {integration.lower()}")
        self.code = "INTEGRATION_NOT_CONFIGURED"

class IntegrationError(ToolError):
    def __init__(self, tool: str, message: str):
        super().__init__(tool, message)
        self.code = "INTEGRATION_ERROR"

# Database
class DatabaseError(SynapzeError):
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, "DATABASE_ERROR", details)

class RecordNotFoundError(DatabaseError):
    def __init__(self, entity: str, id: str):
        super().__init__(f"{entity} not found: {id}", {"entity": entity, "id": id})
        self.code = "NOT_FOUND"

# Validation
class ValidationError(SynapzeError):
    def __init__(self, field: str, message: str):
        super().__init__(message, "VALIDATION_ERROR", {"field": field})

# Rate limit
class RateLimitError(SynapzeError):
    def __init__(self, retry_after: int = 60):
        super().__init__(
            f"Rate limit exceeded. Retry after {retry_after}s",
            "RATE_LIMIT",
            {"retry_after_secs": retry_after}
        )
