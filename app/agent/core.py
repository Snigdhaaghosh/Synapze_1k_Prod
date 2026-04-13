"""
Synapze Enterprise — Core Agent
Tuned for 1,000 concurrent users:
- Module-level Anthropic client singleton (connection pool reuse)
- Module-level MemoryManager singleton (shared Redis pool)
- Process-level semaphore caps concurrent Anthropic calls
- Streaming releases DB connections before yielding (no connection held during stream)
- Exponential backoff on Anthropic rate limits
"""
import asyncio
import json
import time
from typing import AsyncGenerator, Optional

import anthropic
from anthropic import APIConnectionError, APIStatusError, RateLimitError as AnthropicRateLimitError

from app.agent.memory import get_memory_manager
from app.agent.registry import TOOL_DEFINITIONS, ToolExecutor
from app.config import settings
from app.core.logging import get_logger
from app.db.database import audit, record_usage
from app.monitoring.metrics import record_agent_run, record_tokens, record_tool_call

logger = get_logger("agent")

# ── Module-level singletons (created once per process) ──────────────────────────

_anthropic_client: Optional[anthropic.AsyncAnthropic] = None
_anthropic_semaphore: Optional[asyncio.Semaphore] = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=settings.ANTHROPIC_API_TIMEOUT_SECS,
            max_retries=settings.ANTHROPIC_MAX_RETRIES,
            # httpx connection pool: keep connections warm across requests
            http_client=None,  # anthropic creates its own httpx client with pooling
        )
    return _anthropic_client


def _get_semaphore() -> asyncio.Semaphore:
    """Cap concurrent Anthropic API calls per process to avoid hammering the API."""
    global _anthropic_semaphore
    if _anthropic_semaphore is None:
        _anthropic_semaphore = asyncio.Semaphore(settings.ANTHROPIC_MAX_CONCURRENT)
    return _anthropic_semaphore


SYSTEM_PROMPT = """You are Synapze, an autonomous AI co-worker.

Your capabilities:
- Gmail: read, send, reply, search emails
- Google Calendar: view, create, update, delete events
- WhatsApp: send and receive messages
- Slack: post messages, read channels
- Browser: navigate any website and perform actions
- Memory: save and recall information across sessions

Operating rules:
1. Always confirm before sending emails or messages unless user said "auto" or "just do it"
2. Check calendar conflicts before creating events
3. When reading emails, summarize — don't dump the full content unless asked
4. For multi-step tasks, state your plan first, then execute
5. If a tool fails, tell the user clearly and suggest alternatives
6. Never expose API keys, tokens, or credentials
7. When memory search returns relevant context, use it silently
8. Be concise — this is a professional tool, not a chat app
9. If unsure about a destructive action (delete, send, post), always confirm first"""

_RETRYABLE = (APIConnectionError, asyncio.TimeoutError)


class SynapzeAgent:
    """
    Stateless agent — safe to instantiate per request.
    All expensive resources (client, memory, semaphore) are singletons.
    """

    def __init__(self):
        self.client   = _get_client()
        self.memory   = get_memory_manager()
        self.executor = ToolExecutor()
        self._sem     = _get_semaphore()

    async def run(self, message: str, user_id: str, session_id: str,
                  stream: bool = True) -> dict | AsyncGenerator[str, None]:
        if stream:
            return self._stream(message, user_id, session_id)
        return await self._run_sync(message, user_id, session_id)

    # ── Sync mode ──────────────────────────────────────────────────────────────

    async def _run_sync(self, user_message: str, user_id: str, session_id: str) -> dict:
        start    = time.monotonic()
        history  = await self.memory.get_history(session_id)
        context  = await self.memory.get_user_context(user_id)
        system   = self._build_system(context)
        messages = history + [{"role": "user", "content": user_message}]

        tool_calls_log = []
        final_text     = ""
        tokens_in = tokens_out = 0

        for iteration in range(settings.AGENT_MAX_ITERATIONS):
            try:
                async with self._sem:
                    response = await self.client.messages.create(
                        model=settings.ANTHROPIC_MODEL,
                        max_tokens=settings.ANTHROPIC_MAX_TOKENS,
                        system=system,
                        tools=TOOL_DEFINITIONS,
                        messages=messages,
                    )
            except AnthropicRateLimitError:
                wait = min(60 * (2 ** min(iteration, 3)), 300)
                logger.warning(f"Anthropic rate limit — waiting {wait}s")
                await asyncio.sleep(wait)
                continue
            except _RETRYABLE as e:
                logger.error(f"Anthropic transient error: {e}")
                final_text = "A temporary error occurred. Please try again."
                break
            except APIStatusError as e:
                logger.error(f"Anthropic API error: {e.status_code}")
                final_text = "An API error occurred. Please try again."
                break

            tokens_in  += response.usage.input_tokens
            tokens_out += response.usage.output_tokens

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        final_text = block.text
                break

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    t0 = time.monotonic()
                    result  = await self.executor.execute(block.name, block.input, user_id)
                    t_ms    = round((time.monotonic() - t0) * 1000)
                    success = "error" not in result
                    tool_calls_log.append({"tool": block.name, "success": success})
                    record_tool_call(block.name, "ok" if success else "error", t_ms)
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                         "content": json.dumps(result)})
                messages.append({"role": "user", "content": tool_results})
            else:
                break
        else:
            final_text = (f"Task required more than {settings.AGENT_MAX_ITERATIONS} steps. "
                          "Please break it into smaller parts.")

        duration = round(time.monotonic() - start, 2)
        total    = tokens_in + tokens_out
        record_agent_run("sync", "ok", duration)
        record_tokens(tokens_in, tokens_out)

        # Fire-and-forget persistence — don't block the response
        asyncio.create_task(self._persist(
            session_id, user_id, user_message, final_text,
            tool_calls_log, total, tokens_in, tokens_out
        ))

        return {
            "response": final_text, "session_id": session_id,
            "tool_calls": tool_calls_log, "tokens_used": total,
            "duration_ms": round(duration * 1000),
        }

    # ── Stream mode ────────────────────────────────────────────────────────────

    async def _stream(self, user_message: str, user_id: str,
                      session_id: str) -> AsyncGenerator[str, None]:
        # !! CRITICAL: Load ALL data from DB BEFORE starting to yield.
        # This releases DB connections before the long-running stream begins.
        history = await self.memory.get_history(session_id)
        context = await self.memory.get_user_context(user_id)
        system  = self._build_system(context)
        messages = history + [{"role": "user", "content": user_message}]
        # DB connections fully released here — stream holds NO DB connection.

        start          = time.monotonic()
        tool_calls_log = []
        full_response  = ""
        tokens_in = tokens_out = 0

        def emit(data: dict) -> str:
            return json.dumps(data, ensure_ascii=False) + "\n"

        try:
            from app.monitoring.metrics import ACTIVE_STREAMS
            ACTIVE_STREAMS.inc()
        except Exception:
            pass

        try:
            for iteration in range(settings.AGENT_MAX_ITERATIONS):
                pending_tool_uses: list[dict] = []

                try:
                    async with self._sem:
                        async with self.client.messages.stream(
                            model=settings.ANTHROPIC_MODEL,
                            max_tokens=settings.ANTHROPIC_MAX_TOKENS,
                            system=system, tools=TOOL_DEFINITIONS,
                            messages=messages,
                        ) as stream:
                            cur_block_id = cur_tool = cur_input = ""

                            async for event in stream:
                                et = event.type
                                if et == "content_block_start":
                                    cb = event.content_block
                                    if cb.type == "tool_use":
                                        cur_block_id = cb.id
                                        cur_tool     = cb.name
                                        cur_input    = ""
                                        yield emit({"type": "tool_start", "tool": cb.name})

                                elif et == "content_block_delta":
                                    d = event.delta
                                    if hasattr(d, "text"):
                                        full_response += d.text
                                        yield emit({"type": "text", "chunk": d.text})
                                    elif hasattr(d, "partial_json"):
                                        cur_input += d.partial_json

                                elif et == "content_block_stop":
                                    if cur_block_id and cur_tool:
                                        try:
                                            parsed = json.loads(cur_input) if cur_input else {}
                                        except json.JSONDecodeError:
                                            parsed = {}
                                        pending_tool_uses.append(
                                            {"id": cur_block_id, "name": cur_tool, "input": parsed}
                                        )
                                        cur_block_id = cur_tool = ""

                                elif et == "message_delta" and hasattr(event, "usage"):
                                    tokens_out += getattr(event.usage, "output_tokens", 0)

                            final_msg   = await stream.get_final_message()
                            tokens_in  += final_msg.usage.input_tokens

                except AnthropicRateLimitError:
                    yield emit({"type": "error",
                                "message": "Rate limit reached — please wait 60s and try again"})
                    break
                except _RETRYABLE:
                    yield emit({"type": "error", "message": "Connection error. Please retry."})
                    break
                except anthropic.APIError as e:
                    yield emit({"type": "error", "message": f"API error: {str(e)[:200]}"})
                    break

                if not pending_tool_uses:
                    break

                messages.append({"role": "assistant", "content": final_msg.content})
                tool_results = []
                for tu in pending_tool_uses:
                    yield emit({"type": "tool_start", "tool": tu["name"], "input": tu["input"]})
                    t0     = time.monotonic()
                    result = await self.executor.execute(tu["name"], tu["input"], user_id)
                    t_ms   = round((time.monotonic() - t0) * 1000)
                    ok     = "error" not in result
                    tool_calls_log.append({"tool": tu["name"], "success": ok})
                    record_tool_call(tu["name"], "ok" if ok else "error", t_ms)
                    yield emit({"type": "tool_result", "tool": tu["name"],
                                "result": result, "success": ok})
                    tool_results.append({"type": "tool_result", "tool_use_id": tu["id"],
                                         "content": json.dumps(result)})
                messages.append({"role": "user", "content": tool_results})

            else:
                yield emit({"type": "error",
                            "message": f"Task too complex (>{settings.AGENT_MAX_ITERATIONS} steps)."})

        finally:
            try:
                from app.monitoring.metrics import ACTIVE_STREAMS
                ACTIVE_STREAMS.dec()
            except Exception:
                pass

        duration = round(time.monotonic() - start, 2)
        total    = tokens_in + tokens_out
        record_agent_run("stream", "ok", duration)
        record_tokens(tokens_in, tokens_out)

        yield emit({"type": "done", "tool_calls": len(tool_calls_log),
                    "tokens": total, "duration_ms": round(duration * 1000)})

        # Persist after stream ends — DB connection acquired AFTER releasing to client
        asyncio.create_task(self._persist(
            session_id, user_id, user_message, full_response,
            tool_calls_log, total, tokens_in, tokens_out
        ))

    async def _persist(self, session_id, user_id, user_msg, assistant_msg,
                       tool_calls, total, tokens_in, tokens_out):
        """Fire-and-forget: save turn + usage + audit in background."""
        try:
            await self.memory.save_turn(
                session_id=session_id, user_id=user_id,
                user_message=user_msg, assistant_message=assistant_msg,
                tool_calls=tool_calls, token_count=total,
            )
            await record_usage(user_id=user_id, tokens_in=tokens_in,
                               tokens_out=tokens_out, tool_calls=len(tool_calls))
            await audit(user_id=user_id, action="agent_run",
                        details={"session_id": session_id, "tokens": total})
        except Exception as e:
            logger.error(f"Persistence failed (non-fatal): {e}")

    def _build_system(self, context: dict) -> str:
        system = SYSTEM_PROMPT
        if context:
            system += f"\n\nYour knowledge about this user:\n{json.dumps(context, indent=2, ensure_ascii=False)}"
        return system
