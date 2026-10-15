"""The Celery app and task names the worker containers and queued messages depend on.

E0-10 moved the tasks from ``workers/`` to ``app/modules/ingestion/tasks.py`` and the Celery
CLI target from ``workers.celery_app`` to ``app.platform.celery_app``. The registered task names
and routes must stay exactly as they were, or messages already in Redis (and Flower's history)
would point at tasks no worker knows.
"""

from celery.app.utils import find_app

from app.modules.ingestion import tasks
from app.platform import celery_app as platform_celery_app

TASK_NAMES = [
    "workers.ai_task.process_candidate_ai",
    "workers.extract_task.extract_resume",
]
ROUTES = {
    "workers.extract_task.extract_resume": {"queue": "extract_queue"},
    "workers.ai_task.process_candidate_ai": {"queue": "ai_queue"},
}


def test_celery_cli_target_registers_the_tasks_under_their_original_names() -> None:
    app = find_app("app.platform.celery_app")  # what `celery -A app.platform.celery_app` resolves
    assert app is platform_celery_app.celery_app

    app.loader.import_default_modules()  # the `include` list, as a worker does at startup
    registered = sorted(name for name in app.tasks if not name.startswith("celery."))
    assert registered == TASK_NAMES
    assert app.conf.task_routes == ROUTES


def test_task_objects_use_the_original_names_and_queues() -> None:
    assert tasks.extract_resume.name == tasks.EXTRACT_TASK_NAME == TASK_NAMES[1]
    assert tasks.process_candidate_ai.name == tasks.AI_TASK_NAME == TASK_NAMES[0]
    assert tasks.extract_resume.queue == "extract_queue"
    assert tasks.process_candidate_ai.queue == "ai_queue"
    assert (tasks.extract_resume.max_retries, tasks.extract_resume.default_retry_delay) == (3, 30)
    assert (
        tasks.process_candidate_ai.max_retries,
        tasks.process_candidate_ai.default_retry_delay,
    ) == (
        3,
        60,
    )
