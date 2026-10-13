"""Checks that the test harness in ``tests/conftest.py`` does what it promises."""

from alembic.config import Config
from alembic.script import ScriptDirectory
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models.campaign import Campaign
from app.platform import storage
from tests import postgres
from tests.fakes import CeleryRecorder, InMemoryStorage
from workers.ai_task import process_candidate_ai
from workers.extract_task import extract_resume


async def test_migrations_are_at_head(db_session: AsyncSession) -> None:
    cfg = Config(str(postgres.ALEMBIC_INI))
    cfg.set_main_option("script_location", str(postgres.BACKEND_DIR / "alembic"))
    head = ScriptDirectory.from_config(cfg).get_current_head()

    version = (await db_session.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    assert version == head


async def test_commit_is_rolled_back_after_the_test(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    # The endpoint commits; the test's session sees the row...
    resp = await client.post("/api/v1/campaigns", json={"title": "Tx", "role": "Dev"})
    assert resp.status_code == 201
    count = select(func.count()).select_from(Campaign).where(Campaign.title == "Tx")
    assert (await db_session.execute(count)).scalar_one() == 1

    # ...but it only released a savepoint: another connection cannot see it.
    async with db_engine.connect() as other:
        assert (await other.execute(count)).scalar_one() == 0


async def test_database_starts_empty(db_session: AsyncSession) -> None:
    assert (await db_session.execute(select(func.count()).select_from(Campaign))).scalar_one() == 0


def test_celery_dispatch_is_recorded(celery_calls: CeleryRecorder) -> None:
    result = extract_resume.apply_async(args=["r-1"], queue="extract_queue")
    process_candidate_ai.delay("r-2")

    assert [c.task for c in celery_calls.calls] == [
        "workers.extract_task.extract_resume",
        "workers.ai_task.process_candidate_ai",
    ]
    first = celery_calls.for_task("workers.extract_task.extract_resume")[0]
    assert first.args == ("r-1",)
    assert first.options == {"queue": "extract_queue"}
    assert result.id == first.id
    assert celery_calls.for_task("workers.ai_task.process_candidate_ai")[0].args == ("r-2",)


def test_storage_is_in_memory(fake_storage: InMemoryStorage) -> None:
    key = storage.upload_file(b"%PDF-1.4", "resumes/a.pdf")
    assert storage.download_file(key) == b"%PDF-1.4"
    assert fake_storage.objects[key].content_type == "application/pdf"

    storage.delete_file(key)
    assert key not in fake_storage.objects


async def test_lifespan_does_not_run(client: AsyncClient, fake_storage: InMemoryStorage) -> None:
    # ASGITransport sends no lifespan events, so startup (ensure_bucket_exists) never runs.
    assert (await client.get("/health")).status_code == 200
    assert fake_storage.bucket_ensured is False
