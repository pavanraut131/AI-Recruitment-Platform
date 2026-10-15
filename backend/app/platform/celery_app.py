from celery import Celery

from app.platform.config import settings

celery_app = Celery(
    "resumeai",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    # Imported by worker processes at startup (a string, so platform imports no module). The
    # task names below predate the module layout and are kept on purpose; see that module.
    include=["app.modules.ingestion.tasks"],
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Routing
    task_routes={
        "workers.extract_task.extract_resume": {"queue": "extract_queue"},
        "workers.ai_task.process_candidate_ai": {"queue": "ai_queue"},
    },
    # Retry config
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_max_retries=3,
    # Result expiry
    result_expires=86400,  # 24 hours
    # Worker optimizations
    worker_prefetch_multiplier=1,
    task_always_eager=False,
    # Monitoring
    worker_send_task_events=True,
    task_send_sent_event=True,
)
