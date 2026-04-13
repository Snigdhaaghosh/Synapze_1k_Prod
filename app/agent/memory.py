"""
Synapze Enterprise — Memory Manager singleton
One instance per process, shared Redis pool.
"""
import json
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis

from app.config import settings
from app.core.logging import get_logger
from app.db import database as db

logger = get_logger("memory")

# ── Module-level singleton ─────────────────────────────────────────────────────

_memory_manager = None

def get_memory_manager() -> "MemoryManager":
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager


class MemoryManager:
    """
    Instantiated once per process. Redis pool shared with SecurityMiddleware.
    """

    def _get_redis(self) -> aioredis.Redis:
        from app.core.security import get_redis_client
        return get_redis_client()

    async def get_history(self, session_id: str, limit: int = 20) -> list:
        try:
            r = self._get_redis()
            raw = await r.lrange(f"hist:{session_id}", -(limit * 2), -1)
            if raw:
                return [json.loads(item) for item in raw]
        except Exception as e:
            logger.warning(f"Redis history miss: {e}")
        rows = await db.get_history(session_id, limit=limit)
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    async def save_turn(self, session_id: str, user_id: str, user_message: str,
                        assistant_message: str, tool_calls: list,
                        token_count: Optional[int] = None) -> None:
        try:
            r = self._get_redis()
            key  = f"hist:{session_id}"
            pipe = r.pipeline(transaction=False)
            pipe.rpush(key,
                json.dumps({"role": "user",      "content": user_message}),
                json.dumps({"role": "assistant", "content": assistant_message}),
            )
            pipe.expire(key, 60 * 60 * 24 * 7)
            await pipe.execute()
        except Exception as e:
            logger.warning(f"Redis save failed (non-fatal): {e}")

        await db.save_message(session_id, "user", user_message, [])
        await db.save_message(session_id, "assistant", assistant_message, tool_calls, token_count)

        if settings.FEATURE_WORLD_MODEL:
            try:
                await self._extract_and_update_world_model(user_id, user_message, assistant_message)
            except Exception as e:
                logger.debug(f"World model update skipped: {e}")

    async def save_memory(self, user_id: str, key: str, content: str,
                          category: str = "note") -> dict:
        embedding = await self._get_embedding(content) if settings.FEATURE_SEMANTIC_MEMORY else None
        await db.save_memory(user_id, key, content, category, embedding)
        return {"success": True, "key": key, "category": category}

    async def search(self, user_id: str, query: str, limit: int = 10) -> dict:
        semantic = []
        if settings.FEATURE_SEMANTIC_MEMORY:
            try:
                embedding = await self._get_embedding(query)
                if embedding:
                    semantic = await db.search_memory_semantic(user_id, embedding, limit=limit)
            except Exception as e:
                logger.warning(f"Semantic search failed: {e}")
        results = semantic or await db.search_memory_text(user_id, query, limit=limit)
        return {
            "results": [{"key": r["key"], "content": r["content"], "category": r["category"],
                         "relevance": round(float(r.get("similarity", r.get("score", 0.5))), 3)}
                        for r in results],
            "count": len(results),
        }

    async def get_user_context(self, user_id: str) -> dict:
        if not settings.FEATURE_WORLD_MODEL:
            return {}
        try:
            r = self._get_redis()
            cached = await r.get(f"wm:{user_id}")
            if cached:
                return json.loads(cached)
            user = await db.get_user(user_id)
            wm   = user.get("world_model") or {}
            if isinstance(wm, str):
                wm = json.loads(wm)
            await r.setex(f"wm:{user_id}", 300, json.dumps(wm))
            return wm
        except Exception:
            return {}

    async def _extract_and_update_world_model(self, user_id, user_msg, agent_msg):
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        try:
            resp = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[{"role": "user", "content": (
                    "Extract new factual updates from this conversation. "
                    "Return ONLY valid JSON or {}. Extract: names, relationships, preferences, tasks.\n\n"
                    f"User: {user_msg[:400]}\nAgent: {agent_msg[:400]}\n\nJSON:"
                )}],
            )
            raw = resp.content[0].text.strip()
            if not raw or raw == "{}":
                return
            new_facts = json.loads(raw)
            if not new_facts:
                return
            user   = await db.get_user(user_id)
            existing = user.get("world_model") or {}
            if isinstance(existing, str):
                existing = json.loads(existing)
            merged = self._deep_merge(existing, new_facts)
            merged["_updated_at"] = datetime.now(timezone.utc).isoformat()
            await db.update_user_field(user_id, "world_model", json.dumps(merged))
            try:
                r = self._get_redis()
                await r.delete(f"wm:{user_id}")
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"World model extraction skipped: {e}")

    def _deep_merge(self, base: dict, update: dict) -> dict:
        result = base.copy()
        for k, v in update.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = self._deep_merge(result[k], v)
            else:
                result[k] = v
        return result

    async def _get_embedding(self, text: str) -> Optional[list]:
        if not settings.OPENAI_API_KEY:
            return None
        try:
            import openai
            client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            resp   = await client.embeddings.create(model="text-embedding-3-small", input=text[:8000])
            return resp.data[0].embedding
        except Exception as e:
            logger.warning(f"Embedding failed: {e}")
            return None
