from celery import Celery

from packages.platform.config import get_settings

settings = get_settings()
celery_app = Celery(
    "semantica_enterprise",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["apps.worker.tasks"],
)
celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_always_eager=settings.celery_always_eager,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    beat_schedule={
        "schedule-due-sources": {
            "task": "sources.schedule_due",
            "schedule": settings.source_scheduler_seconds,
        }
    },
)
