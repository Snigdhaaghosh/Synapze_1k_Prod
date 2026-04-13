"""Synapze Enterprise — Celery, tuned for 1k users."""
from celery import Celery
from celery.schedules import crontab
from app.config import settings

celery_app = Celery(
    "synapze_enterprise",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks.jobs"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=True,
    # Reliability
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    # Limits
    task_soft_time_limit=settings.CELERY_TASK_SOFT_LIMIT,
    task_time_limit=settings.CELERY_TASK_HARD_LIMIT,
    # Worker memory management
    worker_max_tasks_per_child=settings.CELERY_MAX_TASKS_PER_CHILD,
    worker_max_memory_per_child=500_000,  # 500MB RSS limit per worker child
    # Results
    result_expires=86400,
    result_compression="gzip",
    # Broker connection pool
    broker_pool_limit=settings.CELERY_WORKER_CONCURRENCY,
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=10,
    # Redbeat scheduler
    redbeat_redis_url=settings.CELERY_BROKER_URL,
    redbeat_key_prefix="synapze:beat:",
    redbeat_lock_timeout=settings.MONITOR_EMAIL_INTERVAL_MINS * 60 * 5,
    # Queues
    task_default_queue="celery",
    task_queues={
        "celery": {"routing_key": "celery"},
        "high_priority": {"routing_key": "high_priority"},
    },
    # Beat schedules
    beat_schedule={
        "poll-emails": {
            "task": "app.tasks.jobs.poll_emails",
            "schedule": crontab(minute=f"*/{settings.MONITOR_EMAIL_INTERVAL_MINS}"),
        },
        "daily-briefing": {
            "task": "app.tasks.jobs.daily_briefing",
            "schedule": crontab(hour=9, minute=0),
        },
        "cleanup-old-sessions": {
            "task": "app.tasks.jobs.cleanup_old_sessions",
            "schedule": crontab(hour=2, minute=0),
        },
        "vacuum-usage-metrics": {
            "task": "app.tasks.jobs.vacuum_usage_metrics",
            "schedule": crontab(hour=3, minute=0, day_of_week=0),  # weekly Sunday 3am
        },
    },
)
