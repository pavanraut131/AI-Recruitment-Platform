"""Contract: /api/v2/stats and /api/v2/home (and their ``client_id`` filter, E2-06)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from syrupy.assertion import SnapshotAssertion

from app.api.v2 import dashboard
from app.modules.talent.models import Person
from tests.contract_v2.data import V2, applicant
from tests.default_agency import DEFAULT_AGENCY_ID
from tests.factories import at, make_campaign, make_client, make_resume
from tests.snapshot_utils import response_data


async def _workspace(db: AsyncSession) -> None:
    """Two active campaigns (one updated later), a closed and an archived one; resumes in
    progress in two campaigns; strong matches in two campaigns and one weak match."""
    backend = await make_campaign(db, title="Backend", updated_at=at(5))
    data = await make_campaign(db, title="Data", updated_at=at(50))
    closed = await make_campaign(db, title="Closed", status="closed")
    await make_campaign(db, title="Archived", status="archived")
    await make_resume(db, backend, original_filename="queued.pdf", status="pending",
                      uploaded_at=datetime.now(UTC))  # fmt: skip
    await make_resume(db, data, original_filename="reading.pdf", status="extracting",
                      uploaded_at=at(3))  # fmt: skip
    await make_resume(db, data, original_filename="broken.pdf", status="failed", uploaded_at=at(4))
    await applicant(db, backend, 1, name="Ada", score=90.0, category="strong_match")
    await applicant(db, data, 2, name="Grace", score=85.0, category="strong_match",
                    pipeline_stage="phone_call")  # fmt: skip
    await applicant(db, closed, 3, name="Linus", score=20.0, category="weak_match")
    db.add(
        Person(
            full_name="No applications",
            created_at=at(0),
            updated_at=at(0),
            agency_id=DEFAULT_AGENCY_ID,
        )
    )
    await db.flush()


async def test_stats(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    await _workspace(db_session)
    resp = await client.get(f"{V2}/stats")
    assert response_data(resp) == snapshot


async def test_stats_empty(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{V2}/stats")
    assert response_data(resp) == snapshot


async def test_home(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    await _workspace(db_session)
    resp = await client.get(f"{V2}/home")
    assert response_data(resp) == snapshot


async def test_stats_and_home_of_one_client(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    """``client_id`` narrows both to that client's campaigns; the people stay agency-wide."""
    await _workspace(db_session)  # every campaign of it is General's
    acme = await make_client(db_session, "Acme")
    acme_campaign = await make_campaign(db_session, title="Acme's", client_id=acme.id)
    await applicant(db_session, acme_campaign, 9, name="Alan", score=95.0, category="strong_match")
    stats = await client.get(f"{V2}/stats", params={"client_id": str(acme.id)})
    home = await client.get(f"{V2}/home", params={"client_id": str(acme.id)})
    assert [response_data(stats), response_data(home)] == snapshot


async def test_home_empty(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{V2}/home")
    assert response_data(resp) == snapshot


async def test_home_parts_are_bounded(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dashboard, "HOME_CAMPAIGNS", 1)
    monkeypatch.setattr(dashboard, "HOME_PROCESSING", 1)
    monkeypatch.setattr(dashboard, "HOME_MATCHES", 1)
    await _workspace(db_session)
    home = (await client.get(f"{V2}/home")).json()
    assert [c["title"] for c in home["active_campaigns"]] == ["Data"]  # most recently updated
    assert (home["processing"]["total"], len(home["processing"]["items"])) == (2, 1)
    assert home["processing"]["items"][0]["filename"] == "queued.pdf"  # newest upload
    assert [m["person"]["name"] for m in home["recent_strong_matches"]] == ["Grace"]
