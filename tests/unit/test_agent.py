"""Unit tests — Agent core"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestSynapzeAgent:
    @pytest.fixture
    def agent(self):
        with patch("app.agent.core.anthropic.AsyncAnthropic"):
            from app.agent.core import SynapzeAgent
            return SynapzeAgent()

    @pytest.mark.asyncio
    async def test_run_sync_returns_dict(self, agent, mock_anthropic_response):
        """Agent run in sync mode returns expected dict shape."""
        agent.client.messages.create = AsyncMock(return_value=mock_anthropic_response)
        agent.memory.get_history = AsyncMock(return_value=[])
        agent.memory.get_user_context = AsyncMock(return_value={})
        agent.memory.save_turn = AsyncMock()

        with patch("app.agent.core.audit", new_callable=AsyncMock), \
             patch("app.agent.core.record_usage", new_callable=AsyncMock), \
             patch("app.agent.core.record_agent_run"), \
             patch("app.agent.core.record_tokens"):
            result = await agent._run_sync("test message", "user123", "session456")

        assert "response" in result
        assert "session_id" in result
        assert "tokens_used" in result
        assert result["session_id"] == "session456"
        assert result["response"] == "Test response from agent"

    @pytest.mark.asyncio
    async def test_run_sync_handles_api_timeout(self, agent):
        """Agent handles Anthropic API timeout gracefully."""
        import asyncio
        agent.client.messages.create = AsyncMock(side_effect=asyncio.TimeoutError())
        agent.memory.get_history = AsyncMock(return_value=[])
        agent.memory.get_user_context = AsyncMock(return_value={})
        agent.memory.save_turn = AsyncMock()

        with patch("app.agent.core.audit", new_callable=AsyncMock), \
             patch("app.agent.core.record_usage", new_callable=AsyncMock), \
             patch("app.agent.core.record_agent_run"), \
             patch("app.agent.core.record_tokens"):
            result = await agent._run_sync("test", "user123", "sess456")

        assert "error" in result["response"].lower() or "timed out" in result["response"].lower() \
               or "temporary" in result["response"].lower()

    @pytest.mark.asyncio
    async def test_run_sync_respects_iteration_limit(self, agent):
        """Agent stops after max iterations when tool use loop runs forever."""
        # Always return tool_use stop reason — agent should eventually hit limit
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "tu_123"
        tool_block.name = "memory_search"
        tool_block.input = {"query": "test"}

        response = MagicMock()
        response.stop_reason = "tool_use"
        response.content = [tool_block]
        response.usage.input_tokens = 10
        response.usage.output_tokens = 10

        agent.client.messages.create = AsyncMock(return_value=response)
        agent.executor.execute = AsyncMock(return_value={"results": []})
        agent.memory.get_history = AsyncMock(return_value=[])
        agent.memory.get_user_context = AsyncMock(return_value={})
        agent.memory.save_turn = AsyncMock()

        with patch("app.agent.core.audit", new_callable=AsyncMock), \
             patch("app.agent.core.record_usage", new_callable=AsyncMock), \
             patch("app.agent.core.record_agent_run"), \
             patch("app.agent.core.record_tokens"):
            result = await agent._run_sync("keep looping", "user123", "sess456")

        assert "steps" in result["response"].lower() or "complex" in result["response"].lower()

    def test_build_system_includes_user_context(self, agent):
        context = {"name": "Rahul", "preferences": {"email_style": "formal"}}
        system = agent._build_system(context)
        assert "Rahul" in system
        assert "formal" in system

    def test_build_system_without_context(self, agent):
        system = agent._build_system({})
        assert "Synapze" in system
        assert len(system) > 100


class TestToolExecutor:
    @pytest.fixture
    def executor(self):
        from app.agent.registry import ToolExecutor
        return ToolExecutor()

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, executor):
        result = await executor.execute("nonexistent_tool", {}, "user123")
        assert "error" in result
        assert "TOOL_NOT_FOUND" in str(result.get("code", ""))

    @pytest.mark.asyncio
    async def test_integration_not_configured_returns_error(self, executor):
        """Gmail tool without tokens should return config error, not crash."""
        with patch("app.tools.gmail.GmailTool._get_service",
                   new_callable=AsyncMock,
                   side_effect=Exception("IntegrationNotConfiguredError")):
            result = await executor.execute("gmail_list_emails", {}, "user123")
        assert "error" in result
