"""Synapze Enterprise — Background jobs"""
import asyncio
import logging
from app.tasks.worker import celery_app

logger = logging.getLogger("synapze.jobs")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="app.tasks.jobs.run_scheduled_task", bind=True, max_retries=3)
def run_scheduled_task(self, user_id: str, session_id: str, message: str):
    async def _run_task():
        from app.agent.core import SynapzeAgent
        agent = SynapzeAgent()
        return await agent.run(message=message, user_id=user_id,
                               session_id=session_id, stream=False)
    try:
        return _run(_run_task())
    except Exception as exc:
        logger.error(f"Scheduled task failed: {exc}")
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@celery_app.task(name="app.tasks.jobs.poll_emails", bind=True, max_retries=1)
def poll_emails(self):
    async def _poll():
        from app.db.database import get_all_users
        from app.tools.gmail import GmailTool
        import redis.asyncio as aioredis
        from app.config import settings

        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        users = await get_all_users(active_since_days=7)
        for user in users:
            try:
                gmail = GmailTool(user_id=user["user_id"])
                result = await gmail.list_emails(query="is:unread", max_results=5)
                await r.setex(f"unread:{user['user_id']}", 600, str(result.get("count", 0)))
            except Exception as e:
                logger.debug(f"Email poll skipped for {user['email']}: {e}")
    try:
        _run(_poll())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="app.tasks.jobs.daily_briefing", bind=True, max_retries=1)
def daily_briefing(self):
    async def _brief():
        from app.db.database import get_all_users
        from app.tools.integrations import CalendarTool, WhatsAppTool
        from app.tools.gmail import GmailTool

        users = await get_all_users(active_since_days=30)
        for user in users:
            if not user.get("whatsapp_number"):
                continue
            try:
                cal = CalendarTool(user_id=user["user_id"])
                gmail = GmailTool(user_id=user["user_id"])
                events = await cal.list_events(days_ahead=1)
                emails = await gmail.list_emails(query="is:unread", max_results=5)

                lines = "\n".join(
                    f"  {e['title']} @ {e['start'][:16]}"
                    for e in events.get("events", [])[:5]
                ) or "  No events today"

                msg = (
                    f"*Synapze Morning Briefing*\n\n"
                    f"*Today:*\n{lines}\n\n"
                    f"*Unread:* {emails.get('count', 0)} emails"
                )
                wa = WhatsAppTool(user_id=user["user_id"])
                await wa.send_message(to=user["whatsapp_number"], message=msg)
            except Exception as e:
                logger.debug(f"Briefing failed for {user['email']}: {e}")
    try:
        _run(_brief())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="app.tasks.jobs.cleanup_old_sessions", bind=True, max_retries=1)
def cleanup_old_sessions(self):
    """Archive sessions inactive for >90 days to keep DB lean."""
    async def _cleanup():
        from app.db.database import get_pool
        pool = await get_pool()
        result = await pool.fetchval("""
            UPDATE sessions
            SET is_archived = TRUE
            WHERE NOT is_archived
              AND last_active < NOW() - INTERVAL '90 days'
            RETURNING count(*)
        """)
        if result:
            logger.info(f"Archived {result} old sessions")
    try:
        _run(_cleanup())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="app.tasks.jobs.vacuum_usage_metrics", bind=True, max_retries=1)
def vacuum_usage_metrics(self):
    """Delete usage_metrics older than 90 days to keep the table lean."""
    async def _vacuum():
        from app.db.database import get_pool
        pool = await get_pool()
        deleted = await pool.fetchval("""
            DELETE FROM usage_metrics WHERE date < CURRENT_DATE - 90 RETURNING COUNT(*)
        """)
        logger.info(f"Vacuumed {deleted or 0} old usage_metrics rows")
    try:
        _run(_vacuum())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=3600)
