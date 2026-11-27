"""Shared state per agency (EPIC-02 spec §6.3, §6.5, §11.2 ``test_sse_tenant``): Redis channels
name the agency, another agency's campaign stream is not found (under row-level security, even
before any filter), new uploads are stored under the agency's prefix, and the orphan check scans
the old and the new prefixes."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import AppStatus

from app.devtools import storage_orphans
from app.modules.ingestion import service as ingestion_service
from app.modules.ingestion.events import campaign_channel, publish_resume_event
from tests.default_agency import DEFAULT_AGENCY_ID
from tests.factories import make_campaign
from tests.fakes import InMemoryStorage
from tests.sse import read_sse
from tests.tenancy.conftest import OtherAgency

OTHER_AGENCY = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")


@pytest.fixture(autouse=True)
def _reset_sse_app_status() -> Iterator[None]:
    # sse-starlette keeps a process-wide anyio.Event bound to the first event loop that used it;
    # each test has its own loop (as in tests/contract_v2/conftest.py).
    AppStatus.should_exit_event = None
    yield
    AppStatus.should_exit_event = None


def test_the_channel_names_the_agency_and_the_campaign() -> None:
    campaign_id = uuid.uuid4()
    assert campaign_channel(DEFAULT_AGENCY_ID, campaign_id) == (
        f"agency:{DEFAULT_AGENCY_ID}:campaign:{campaign_id}:events"
    )
    assert campaign_channel(OTHER_AGENCY, campaign_id) != campaign_channel(
        DEFAULT_AGENCY_ID, campaign_id
    )


def _publish_once_connected(
    publishers: list[Callable[[], None]],
) -> Callable[[dict[str, Any]], None]:
    def on_event(event: dict[str, Any]) -> None:
        if event["event"] == "connected":
            for publish in publishers:
                publish()

    return on_event


@pytest.mark.parametrize("path", ["/api/v2/campaigns/{id}/events", "/api/v1/campaigns/{id}/stream"])
async def test_a_stream_gets_only_its_agencys_events_for_the_campaign(
    app: FastAPI, client: AsyncClient, db_session: AsyncSession, path: str
) -> None:
    """The same campaign id published under another agency (ids are never reused, but the
    channels do not rely on it) never reaches the default agency's stream."""
    campaign = await make_campaign(db_session)
    theirs, mine = str(uuid.uuid4()), str(uuid.uuid4())
    publishers: list[Callable[[], None]] = [
        lambda: publish_resume_event(
            theirs,
            "extracting",
            {"filename": "t.pdf"},
            agency_id=OTHER_AGENCY,
            campaign_id=campaign.id,
        ),
        lambda: publish_resume_event(
            mine,
            "extracting",
            {"filename": "m.pdf"},
            agency_id=DEFAULT_AGENCY_ID,
            campaign_id=campaign.id,
        ),
    ]
    result = await read_sse(
        app,
        path.format(id=campaign.id),
        count=2,
        on_event=_publish_once_connected(publishers),
    )
    assert result.status_code == 200
    assert [e["event"] for e in result.events] == ["connected", "resume_update"]
    assert result.events[1]["data"]["resume_id"] == mine


async def test_another_agencys_campaign_stream_is_not_found(
    client: AsyncClient, other_agency: OtherAgency
) -> None:
    """v2 only: v1's stream answers 200 for any campaign id (B7, frozen until E2-12); it then
    subscribes to the default agency's channel of that id, which no task of B publishes on."""
    theirs = await make_campaign(other_agency.db, client_id=other_agency.client_id)
    resp = await client.get(f"/api/v2/campaigns/{theirs.id}/events")
    assert resp.status_code == 404


def test_new_uploads_are_stored_under_the_agency() -> None:
    campaign_id, resume_id = uuid.uuid4(), uuid.uuid4()
    assert ingestion_service.resume_storage_key(OTHER_AGENCY, campaign_id, resume_id) == (
        f"agencies/{OTHER_AGENCY}/campaigns/{campaign_id}/resumes/{resume_id}.pdf"
    )
    assert ingestion_service.RESUME_STORAGE_PREFIXES == ("agencies/", "campaigns/")


def test_the_orphan_check_scans_the_old_and_the_new_prefixes(
    fake_storage: InMemoryStorage,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    new_kept = ingestion_service.resume_storage_key(OTHER_AGENCY, uuid.uuid4(), uuid.uuid4())
    new_orphan = ingestion_service.resume_storage_key(DEFAULT_AGENCY_ID, uuid.uuid4(), uuid.uuid4())
    old_kept = f"campaigns/{uuid.uuid4()}/resumes/{uuid.uuid4()}/cv.pdf"
    old_orphan = f"campaigns/{uuid.uuid4()}/resumes/{uuid.uuid4()}/old.pdf"
    unrelated = "exports/report.csv"
    for key in (new_kept, new_orphan, old_kept, old_orphan, unrelated):
        fake_storage.upload_file(b"%PDF-1.4", key)
    # Every agency's rows (an unbound session in the real command).
    monkeypatch.setattr(ingestion_service, "stored_resume_paths", lambda db: {new_kept, old_kept})

    class NoSession:
        def __enter__(self) -> NoSession:
            return self

        def __exit__(self, *_: Any) -> None:
            return None

    code = storage_orphans.main(
        ["--min-age-minutes", "0"],
        session_factory=NoSession,  # type: ignore[arg-type]
    )

    out = capsys.readouterr().out
    assert code == 1
    assert f"orphan {new_orphan}" in out and f"orphan {old_orphan}" in out
    assert new_kept not in out and old_kept not in out and unrelated not in out
