"""Contract: /api/v2 applications (campaign list, export, bulk; detail, update, delete)."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from syrupy.assertion import SnapshotAssertion

from app.modules.hiring.models import Application, LegacyCandidateMap
from app.modules.ingestion.models import Resume
from tests.contract_v2.data import UNKNOWN_ID, V2, applicant, ranked_campaign
from tests.factories import make_campaign, make_resume
from tests.fakes import InMemoryStorage
from tests.snapshot_utils import response_data


def _names(resp_json: dict[str, object]) -> list[object]:
    return [item["person"]["name"] for item in resp_json["items"]]  # type: ignore[attr-defined, index]


# ── GET /campaigns/{id}/applications ──────────────────────────
async def test_list_applications(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    campaign = await ranked_campaign(db_session)
    other = await make_campaign(db_session, title="Other")
    await applicant(db_session, other, 1, score=99.0)  # another campaign: never listed
    resp = await client.get(f"{V2}/campaigns/{campaign.id}/applications")
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        # Default: best score first, unscored last; tied scores by id (both are 91).
        pytest.param("", None, id="default-score-desc"),
        pytest.param("sort=score", None, id="score-asc-nulls-last"),
        pytest.param("sort=name", ["amy brown", "Ben Evans", "Carl Diaz", "Zoe Adams", None],
                     id="name-case-insensitive-unnamed-last"),
        pytest.param("sort=-updated_at", ["Ben Evans", None, "Carl Diaz", "amy brown",
                                          "Zoe Adams"], id="updated-desc"),
        pytest.param("sort=updated_at", ["Zoe Adams", "amy brown", "Carl Diaz", None,
                                         "Ben Evans"], id="updated-asc"),
        pytest.param("category=strong_match&category=weak_match", None, id="categories"),
        pytest.param("stage=screened&stage=offer", ["Ben Evans", "amy brown"], id="stages"),
        pytest.param("min_score=50&max_score=80", ["amy brown"], id="score-range"),
        pytest.param("q=ADAMS", ["Zoe Adams"], id="q-name"),
        pytest.param("q=applicant2@", ["Carl Diaz"], id="q-email"),
        pytest.param("skill=pyth", None, id="skill"),
        pytest.param("min_experience=5", None, id="min-experience"),
        pytest.param("skill=python&sort=name&page=2&page_size=2", ["Zoe Adams"],
                     id="profile-filter-name-sort-page-2"),
        pytest.param("q=nobody", [], id="no-match"),
    ],
)  # fmt: skip
async def test_list_applications_filters_and_sort(
    client: AsyncClient,
    db_session: AsyncSession,
    snapshot: SnapshotAssertion,
    params: str,
    expected: list[str | None] | None,
) -> None:
    campaign = await ranked_campaign(db_session)
    resp = await client.get(f"{V2}/campaigns/{campaign.id}/applications?{params}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    if expected is not None:
        assert _names(body) == expected
    assert {"names": _names(body), "total": body["total"]} == snapshot


async def test_tied_scores_are_ordered_by_id(client: AsyncClient, db_session: AsyncSession) -> None:
    campaign = await ranked_campaign(db_session)
    body = (await client.get(f"{V2}/campaigns/{campaign.id}/applications")).json()
    tied = [item["id"] for item in body["items"] if item["score"] == 91.0]
    assert tied == sorted(tied, key=uuid.UUID)
    assert body["items"][-1]["score"] is None


@pytest.mark.parametrize(
    "params",
    [
        pytest.param("page_size=101", id="page-size-over-100"),
        pytest.param("page=0", id="page-zero"),
        pytest.param("sort=score_desc", id="unknown-sort"),
        pytest.param("category=great", id="unknown-category"),
        pytest.param("stage=interview", id="unknown-stage"),
        pytest.param("min_score=101", id="score-over-100"),
    ],
)
async def test_list_applications_invalid_query(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, params: str
) -> None:
    campaign = await make_campaign(db_session)
    resp = await client.get(f"{V2}/campaigns/{campaign.id}/applications?{params}")
    assert response_data(resp) == snapshot


async def test_list_applications_unknown_campaign(
    client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    # B7 (v1 answers 200 with an empty list).
    resp = await client.get(f"{V2}/campaigns/{UNKNOWN_ID}/applications")
    assert response_data(resp) == snapshot


# ── GET /campaigns/{id}/applications/export ───────────────────
async def test_export_applications(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    campaign = await ranked_campaign(db_session)
    resp = await client.get(f"{V2}/campaigns/{campaign.id}/applications/export")
    data = response_data(resp, "content-disposition")
    # As lines: a multi-line string would be written with a whitespace-only line, which the
    # trailing-whitespace pre-commit hook would strip.
    data["body"] = resp.text.splitlines()
    assert data == snapshot


async def test_export_applies_every_list_filter(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    # B8 (v1's export ignores skill and min_experience): v2 applies them, and the sort.
    campaign = await ranked_campaign(db_session)
    resp = await client.get(
        f"{V2}/campaigns/{campaign.id}/applications/export?skill=python&min_experience=5&sort=name"
    )
    assert resp.text.splitlines()[1:] == snapshot


async def test_export_selection(client: AsyncClient, db_session: AsyncSession) -> None:
    campaign = await ranked_campaign(db_session)
    items = (await client.get(f"{V2}/campaigns/{campaign.id}/applications")).json()["items"]
    chosen = [items[1]["id"], items[2]["id"]]
    resp = await client.get(
        f"{V2}/campaigns/{campaign.id}/applications/export",
        params={"ids": chosen, "category": "strong_match"},
    )
    rows = resp.text.splitlines()[1:]
    assert len(rows) == 1  # the selection AND the filters
    assert rows[0].startswith(items[1]["person"]["name"])


async def test_export_unknown_campaign(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{V2}/campaigns/{UNKNOWN_ID}/applications/export")
    assert response_data(resp) == snapshot


# ── GET / PATCH / DELETE /applications/{id} ───────────────────
async def test_get_application(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    application = await applicant(
        db_session, await make_campaign(db_session), 1, notes="Call back", pipeline_stage="offer"
    )
    resp = await client.get(f"{V2}/applications/{application.id}")
    assert response_data(resp) == snapshot


async def test_get_application_without_extraction(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    application = await applicant(db_session, await make_campaign(db_session), 1)
    resume = await db_session.get(Resume, application.resume_id)
    assert resume is not None
    resume.extracted = None
    await db_session.flush()
    resp = await client.get(f"{V2}/applications/{application.id}")
    assert response_data(resp) == snapshot


async def test_legacy_candidate_ids_do_not_resolve_in_v2(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    application = await applicant(db_session, await make_campaign(db_session), 1)
    merged_id = uuid.uuid4()
    db_session.add(LegacyCandidateMap(candidate_id=merged_id, application_id=application.id))
    await db_session.flush()
    assert (await client.get(f"/api/v1/candidates/{merged_id}")).status_code == 200
    assert (await client.get(f"{V2}/applications/{merged_id}")).status_code == 404


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        pytest.param("GET", {}, id="get"),
        pytest.param("PATCH", {"json": {"notes": "x"}}, id="patch"),
        pytest.param("DELETE", {}, id="delete"),
    ],
)
async def test_unknown_application(
    client: AsyncClient, snapshot: SnapshotAssertion, method: str, kwargs: dict[str, object]
) -> None:
    resp = await client.request(method, f"{V2}/applications/{UNKNOWN_ID}", **kwargs)  # type: ignore[arg-type]
    assert response_data(resp) == snapshot


async def test_update_application(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    application = await applicant(
        db_session, await make_campaign(db_session), 1, notes="old", pipeline_stage="screened"
    )
    resp = await client.patch(
        f"{V2}/applications/{application.id}",
        json={"pipeline_stage": "technical", "category": "moderate_match"},
    )
    assert response_data(resp) == snapshot
    assert resp.json()["notes"] == "old"  # not sent: unchanged

    cleared = await client.patch(
        f"{V2}/applications/{application.id}", json={"pipeline_stage": None, "notes": None}
    )
    assert (cleared.json()["pipeline_stage"], cleared.json()["notes"]) == (None, None)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"pipeline_stage": "interview"}, id="unknown-stage"),
        pytest.param({"category": "great"}, id="unknown-category"),
        pytest.param({"category": None}, id="null-category"),
        pytest.param({"score": 100}, id="unknown-field"),
        pytest.param({"notes": "x" * 10_001}, id="notes-too-long"),
    ],
)
async def test_update_application_invalid(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, payload: object
) -> None:
    application = await applicant(db_session, await make_campaign(db_session), 1)
    resp = await client.patch(f"{V2}/applications/{application.id}", json=payload)
    assert response_data(resp) == snapshot


async def test_delete_application(
    client: AsyncClient, db_session: AsyncSession, fake_storage: InMemoryStorage
) -> None:
    campaign = await make_campaign(db_session)
    application = await applicant(db_session, campaign, 1)
    # An older resume of the same Person in the campaign goes with it (as in v1).
    older = await make_resume(db_session, campaign, original_filename="older.pdf")
    older.person_id = application.person_id
    kept = await applicant(db_session, campaign, 2)
    for resume in (await db_session.execute(select(Resume))).scalars():
        fake_storage.upload_file(b"%PDF", resume.storage_path)

    resp = await client.delete(f"{V2}/applications/{application.id}")

    assert (resp.status_code, resp.content) == (204, b"")
    assert list((await db_session.execute(select(Application.id))).scalars()) == [kept.id]
    remaining = list((await db_session.execute(select(Resume))).scalars())
    assert [r.id for r in remaining] == [kept.resume_id]
    assert list(fake_storage.objects) == [remaining[0].storage_path]


# ── POST /campaigns/{id}/applications/bulk ────────────────────
async def test_bulk_stage_reports_not_found_ids(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    campaign = await make_campaign(db_session)
    first = await applicant(db_session, campaign, 1)
    second = await applicant(db_session, campaign, 2)
    elsewhere = await applicant(db_session, await make_campaign(db_session, title="B"), 3)
    unknown = uuid.UUID(UNKNOWN_ID)
    resp = await client.post(
        f"{V2}/campaigns/{campaign.id}/applications/bulk",
        json={
            "action": "stage",
            "stage": "phone_call",
            "ids": [str(i) for i in (second.id, unknown, first.id, elsewhere.id, second.id)],
        },
    )
    assert {
        "response": response_data(resp),
        "requested": [second.id, unknown, first.id, elsewhere.id],
    } == snapshot
    db_session.expire_all()
    stages = {
        a.id: a.pipeline_stage for a in (await db_session.execute(select(Application))).scalars()
    }
    assert stages == {first.id: "phone_call", second.id: "phone_call", elsewhere.id: None}


async def test_bulk_reject(client: AsyncClient, db_session: AsyncSession) -> None:
    campaign = await make_campaign(db_session)
    application = await applicant(db_session, campaign, 1, pipeline_stage="screened")
    resp = await client.post(
        f"{V2}/campaigns/{campaign.id}/applications/bulk",
        json={"action": "reject", "ids": [str(application.id)]},
    )
    assert resp.json() == {"action": "reject", "affected": [str(application.id)], "not_found": []}
    await db_session.refresh(application)
    assert application.pipeline_stage == "rejected_manual"


async def test_bulk_delete_partial(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_storage: InMemoryStorage,
    snapshot: SnapshotAssertion,
) -> None:
    campaign = await make_campaign(db_session)
    doomed = await applicant(db_session, campaign, 1)
    kept = await applicant(db_session, campaign, 2)
    for resume in (await db_session.execute(select(Resume))).scalars():
        fake_storage.upload_file(b"%PDF", resume.storage_path)
    resp = await client.post(
        f"{V2}/campaigns/{campaign.id}/applications/bulk",
        json={"action": "delete", "ids": [str(doomed.id), UNKNOWN_ID]},
    )
    assert {"response": response_data(resp), "deleted": doomed.id} == snapshot
    assert list((await db_session.execute(select(Application.id))).scalars()) == [kept.id]
    assert [r.id for r in (await db_session.execute(select(Resume))).scalars()] == [kept.resume_id]
    assert len(fake_storage.objects) == 1


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"action": "stage", "ids": [UNKNOWN_ID]}, id="stage-without-stage"),
        pytest.param({"action": "reject", "ids": [UNKNOWN_ID], "stage": "offer"},
                     id="stage-with-reject"),
        pytest.param({"action": "archive", "ids": [UNKNOWN_ID]}, id="unknown-action"),
        pytest.param({"action": "delete", "ids": []}, id="no-ids"),
        pytest.param({"action": "delete", "ids": ["nope"]}, id="invalid-id"),
        pytest.param({"ids": [UNKNOWN_ID]}, id="no-action"),
    ],
)  # fmt: skip
async def test_bulk_invalid(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, payload: object
) -> None:
    campaign = await make_campaign(db_session)
    resp = await client.post(f"{V2}/campaigns/{campaign.id}/applications/bulk", json=payload)
    assert response_data(resp) == snapshot


async def test_bulk_unknown_campaign(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.post(
        f"{V2}/campaigns/{UNKNOWN_ID}/applications/bulk",
        json={"action": "reject", "ids": [UNKNOWN_ID]},
    )
    assert response_data(resp) == snapshot
