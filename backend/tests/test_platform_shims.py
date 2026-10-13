"""The old import paths keep working after the move to ``app.platform`` (E0-08).

The shims re-export the very same objects, and Celery still finds the app and its tasks under
the names that queued messages, the worker containers (``celery -A workers.celery_app``) and the
task routes use. Delete this file together with the shims in E0-10.
"""

import importlib

import pytest
from celery.app.utils import find_app

from app.platform import celery_app as platform_celery_app

SHIMS = {
    "app.core.config": ["Settings", "get_settings", "settings"],
    "app.core.database": ["AsyncSessionLocal", "Base", "engine", "get_db"],
    "app.core.logging": ["configure_logging"],
    "app.core.storage": [
        "delete_file",
        "download_file",
        "ensure_bucket_exists",
        "get_presigned_url",
        "get_s3_client",
        "upload_file",
    ],
    "workers.celery_app": ["celery_app"],
}


@pytest.mark.parametrize("old_path", sorted(SHIMS))
def test_shim_reexports_the_platform_objects(
    old_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The autouse fake_storage fixture patches app.platform.storage; compare against the
    # originals, which are what the shim bound at import.
    monkeypatch.undo()
    new_path = (
        "app.platform.celery_app"
        if old_path == "workers.celery_app"
        else old_path.replace("app.core.", "app.platform.")
    )
    old, new = importlib.import_module(old_path), importlib.import_module(new_path)
    for name in SHIMS[old_path]:
        assert getattr(old, name) is getattr(new, name), f"{old_path}.{name}"


def test_celery_cli_finds_the_platform_app_with_the_same_task_names() -> None:
    app = find_app("workers.celery_app")  # what `celery -A workers.celery_app` resolves
    assert app is platform_celery_app.celery_app

    app.loader.import_default_modules()  # the `include` list, as a worker does at startup
    tasks = sorted(name for name in app.tasks if not name.startswith("celery."))
    assert tasks == [
        "workers.ai_task.process_candidate_ai",
        "workers.extract_task.extract_resume",
    ]
    assert app.conf.task_routes == {
        "workers.extract_task.extract_resume": {"queue": "extract_queue"},
        "workers.ai_task.process_candidate_ai": {"queue": "ai_queue"},
    }
