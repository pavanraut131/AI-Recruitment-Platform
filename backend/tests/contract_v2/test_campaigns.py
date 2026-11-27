"""Contract: /api/v2/campaigns (list, create, detail, update, delete, stats, parse-jd), with the
client each campaign is run for (E2-06)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from syrupy.assertion import SnapshotAssertion

from app.modules.hiring.models import Application, Campaign
from app.modules.ingestion.models import Resume
from app.platform.tenancy import DEFAULT_CLIENT_ID
from tests.characterization.snapshot_utils import response_data
from tests.contract_v2.data import UNKNOWN_ID, V2, applicant, ranked_campaign
from tests.factories import at, make_campaign, make_client, make_resume
from tests.fakes import FakeGemini, InMemoryStorage


async def _campaign_with_activity(db: AsyncSession) -> Campaign:
    """A campaign with resumes in every status (one uploaded "now") and two applications."""
    campaign = await make_campaign(db, title="Busy", required_skills=["python", "Docker"])
    for i, status in enumerate(["pending", "extracting", "processing", "failed", "quota_exceeded"]):
        await make_resume(db, campaign, original_filename=f"{status}.pdf", status=status,
                          uploaded_at=at(i), updated_at=at(i))  # fmt: skip
    await make_resume(
        db, campaign, original_filename="just_now.pdf", status="pending",
        uploaded_at=datetime.now(UTC), updated_at=at(50),
    )  # fmt: skip
    await applicant(db, campaign, 10, score=88.0, category="strong_match",
                    skills=["Python", "Docker"], updated_at=at(60))  # fmt: skip
    await applicant(db, campaign, 11, score=40.0, category="weak_match", skills=["Python"],
                    pipeline_stage="screened")  # fmt: skip
    return campaign


# ── GET /campaigns ────────────────────────────────────────────
async def test_list_campaigns(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    await _campaign_with_activity(db_session)
    await make_campaign(db_session, title="Old closed", status="closed", created_at=at(-10))
    await make_campaign(db_session, title="Newest", created_at=at(10), updated_at=at(10))
    resp = await client.get(f"{V2}/campaigns")
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "params",
    [
        pytest.param("status=closed&status=archived", id="status-any-of"),
        pytest.param("q=BACK", id="q-in-role"),
        pytest.param("q=newest", id="q-in-title"),
        pytest.param("q=%25", id="q-wildcard-literal"),
        pytest.param("page=2&page_size=2", id="page-2"),
    ],
)
async def test_list_campaigns_filters(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, params: str
) -> None:
    await make_campaign(db_session, title="Old closed", status="closed", created_at=at(-10))
    await make_campaign(db_session, title="Archived", status="archived", created_at=at(-5))
    await make_campaign(db_session, title="Active")
    await make_campaign(db_session, title="Newest", role="Designer", created_at=at(10))
    resp = await client.get(f"{V2}/campaigns?{params}")
    body = resp.json()
    assert resp.status_code == 200
    assert {"titles": [c["title"] for c in body["items"]], "total": body["total"],
            "page": body["page"], "page_size": body["page_size"]} == snapshot  # fmt: skip


async def test_list_campaigns_by_client(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    acme = await make_client(db_session, "Acme")
    globex = await make_client(db_session, "Globex")
    await make_campaign(db_session, title="General's")
    await make_campaign(db_session, title="Acme's", client_id=acme.id, created_at=at(1))
    await make_campaign(db_session, title="Globex's", client_id=globex.id, created_at=at(2))
    found = {}
    for name, query in {
        "acme": f"client_id={acme.id}",
        "acme-and-globex": f"client_id={acme.id}&client_id={globex.id}",
        "unknown-client": f"client_id={UNKNOWN_ID}",
    }.items():
        body = (await client.get(f"{V2}/campaigns?{query}")).json()
        found[name] = [(c["title"], c["client"]["name"]) for c in body["items"]]
    assert found == snapshot


@pytest.mark.parametrize(
    "params",
    [
        pytest.param("client_id=acme", id="client-id-not-a-uuid"),
        pytest.param("page=0", id="page-zero"),
        pytest.param("page_size=0", id="page-size-zero"),
        pytest.param("page_size=101", id="page-size-over-100"),
        pytest.param("page=x", id="page-not-a-number"),
        pytest.param("status=paused", id="unknown-status"),
    ],
)
async def test_list_campaigns_invalid_query(
    client: AsyncClient, snapshot: SnapshotAssertion, params: str
) -> None:
    resp = await client.get(f"{V2}/campaigns?{params}")
    assert response_data(resp) == snapshot


# ── POST /campaigns ───────────────────────────────────────────
async def test_create_campaign(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    payload = {
        "title": "  AI Engineer Batch 1 ",
        "role": "AI Engineer",
        "description": "LLM tooling team",
        "required_skills": ["Python", " FastAPI "],
    }
    resp = await client.post(f"{V2}/campaigns", json=payload)
    assert response_data(resp) == snapshot


async def test_create_campaign_minimal(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    """No ``client_id``: the caller's default client, the agency's oldest active one (here the
    migration's "General")."""
    resp = await client.post(f"{V2}/campaigns", json={"title": "Minimal", "role": "Dev"})
    assert response_data(resp) == snapshot


async def test_create_campaign_for_a_client(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    acme = await make_client(db_session, "Acme")
    resp = await client.post(
        f"{V2}/campaigns", json={"title": "For Acme", "role": "Dev", "client_id": str(acme.id)}
    )
    assert response_data(resp) == snapshot
    stored = await db_session.scalar(select(Campaign).where(Campaign.title == "For Acme"))
    assert stored is not None and stored.client_id == acme.id


@pytest.mark.parametrize("case", ["unknown", "archived"])
async def test_create_campaign_for_an_unusable_client(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, case: str
) -> None:
    archived = await make_client(db_session, "Gone", status="archived")
    client_id = {"unknown": UNKNOWN_ID, "archived": str(archived.id)}[case]
    resp = await client.post(
        f"{V2}/campaigns", json={"title": "T", "role": "Dev", "client_id": client_id}
    )
    assert response_data(resp) == snapshot
    assert await db_session.scalar(select(func.count(Campaign.id))) == 0


async def test_create_campaign_with_a_null_client_takes_the_default(client: AsyncClient) -> None:
    resp = await client.post(
        f"{V2}/campaigns", json={"title": "T", "role": "Dev", "client_id": None}
    )
    assert resp.status_code == 201
    assert resp.json()["client"] == {"id": str(DEFAULT_CLIENT_ID), "name": "General"}


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="no-body"),
        pytest.param({}, id="empty-object"),
        pytest.param({"title": "", "role": "Dev"}, id="empty-title"),
        pytest.param({"title": "x" * 256, "role": "Dev"}, id="title-too-long"),
        pytest.param({"title": "T", "role": "Dev", "status": "paused"}, id="unknown-status"),
        pytest.param({"title": "T", "role": "Dev", "required_skills": [""]}, id="empty-skill"),
        pytest.param({"title": "T", "role": "Dev", "owner": "me"}, id="unknown-field"),
        pytest.param({"title": None, "role": "Dev"}, id="null-title"),
    ],
)
async def test_create_campaign_invalid(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, payload: object
) -> None:
    resp = await client.post(f"{V2}/campaigns", json=payload)
    assert response_data(resp) == snapshot
    assert await db_session.scalar(select(func.count(Campaign.id))) == 0


# ── GET /campaigns/{id} ───────────────────────────────────────
async def test_get_campaign(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    campaign = await _campaign_with_activity(db_session)
    resp = await client.get(f"{V2}/campaigns/{campaign.id}")
    assert response_data(resp) == snapshot


async def test_get_campaign_without_activity(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    campaign = await make_campaign(db_session)
    resp = await client.get(f"{V2}/campaigns/{campaign.id}")
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        pytest.param("GET", "", id="get"),
        pytest.param("PATCH", "", id="patch"),
        pytest.param("DELETE", "", id="delete"),
        pytest.param("GET", "/stats", id="stats"),
    ],
)
async def test_unknown_campaign(
    client: AsyncClient, snapshot: SnapshotAssertion, method: str, suffix: str
) -> None:
    kwargs = {"json": {"title": "x"}} if method == "PATCH" else {}
    resp = await client.request(method, f"{V2}/campaigns/{UNKNOWN_ID}{suffix}", **kwargs)
    assert response_data(resp) == snapshot


async def test_invalid_campaign_id(client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    resp = await client.get(f"{V2}/campaigns/not-a-uuid")
    assert response_data(resp) == snapshot


# ── PATCH /campaigns/{id} ─────────────────────────────────────
async def test_update_campaign(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    campaign = await make_campaign(db_session)
    resp = await client.patch(
        f"{V2}/campaigns/{campaign.id}",
        json={"status": "closed", "description": None, "required_skills": ["Go"]},
    )
    assert response_data(resp) == snapshot
    assert resp.json()["title"] == campaign.title  # not sent: unchanged


async def test_move_a_campaign_to_another_client(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    campaign = await make_campaign(db_session)
    acme = await make_client(db_session, "Acme")
    archived = await make_client(db_session, "Gone", status="archived")
    url = f"{V2}/campaigns/{campaign.id}"
    moved = await client.patch(url, json={"client_id": str(acme.id)})
    refused = [
        await client.patch(url, json={"client_id": value})
        for value in (str(archived.id), UNKNOWN_ID, None)
    ]
    assert [response_data(r) for r in (moved, *refused)] == snapshot
    final = (await client.get(url)).json()["client"]
    assert final == {"id": str(acme.id), "name": "Acme"}


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"title": None}, id="null-title"),
        pytest.param({"status": None}, id="null-status"),
        pytest.param({"status": "paused"}, id="unknown-status"),
        pytest.param({"role": ""}, id="empty-role"),
        pytest.param({"required_skills": None}, id="null-skills"),
    ],
)
async def test_update_campaign_invalid(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion, payload: object
) -> None:
    campaign = await make_campaign(db_session)
    resp = await client.patch(f"{V2}/campaigns/{campaign.id}", json=payload)
    assert response_data(resp) == snapshot


# ── DELETE /campaigns/{id} ────────────────────────────────────
async def test_delete_campaign(
    client: AsyncClient, db_session: AsyncSession, fake_storage: InMemoryStorage
) -> None:
    campaign = await _campaign_with_activity(db_session)
    other = await make_campaign(db_session, title="Other")
    kept = await applicant(db_session, other, 1)
    for resume in (await db_session.execute(select(Resume))).scalars():
        fake_storage.upload_file(b"%PDF", resume.storage_path)

    resp = await client.delete(f"{V2}/campaigns/{campaign.id}")

    assert (resp.status_code, resp.content) == (204, b"")
    assert await db_session.scalar(select(func.count(Campaign.id))) == 1
    assert list((await db_session.execute(select(Application.id))).scalars()) == [kept.id]
    resumes = list((await db_session.execute(select(Resume))).scalars())
    assert [r.campaign_id for r in resumes] == [other.id]
    assert list(fake_storage.objects) == [resumes[0].storage_path]


# ── GET /campaigns/{id}/stats ─────────────────────────────────
async def test_campaign_stats(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    campaign = await _campaign_with_activity(db_session)
    resp = await client.get(f"{V2}/campaigns/{campaign.id}/stats")
    assert response_data(resp) == snapshot


async def test_campaign_stats_mean_is_over_scored_applications(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # B9 (v1): avg_score is a mean of per-category means that drops a 0 mean. v2 averages the
    # scored applications: (91 + 74 + 45 + 91) / 4 = 75.25 (the unscored one is left out).
    campaign = await ranked_campaign(db_session)
    await applicant(db_session, campaign, 9, score=0.0, category="rejected")
    stats = (await client.get(f"{V2}/campaigns/{campaign.id}/stats")).json()
    assert stats["avg_score"] == round((91 + 74 + 45 + 91 + 0) / 5, 2)
    assert stats["applications"]["total"] == 6
    assert [b["count"] for b in stats["score_histogram"]] == [1, 0, 0, 0, 1, 0, 0, 1, 0, 2]


async def test_campaign_stats_empty(
    client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    campaign = await make_campaign(db_session)
    resp = await client.get(f"{V2}/campaigns/{campaign.id}/stats")
    assert response_data(resp) == snapshot


# ── POST /campaigns/parse-jd ──────────────────────────────────
JD = "Senior Python Engineer. 5+ years with FastAPI, PostgreSQL and AWS."


async def test_parse_jd(
    client: AsyncClient, fake_gemini: FakeGemini, snapshot: SnapshotAssertion
) -> None:
    fake_gemini.respond_with(
        json.dumps(
            {
                "role_title": "Senior Python Engineer",
                "seniority": "senior",
                "skills": ["Python", "FastAPI", "PostgreSQL", "AWS"],
            }
        )  # fmt: skip
    )
    resp = await client.post(f"{V2}/campaigns/parse-jd", json={"job_description": JD})
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "gemini_response",
    [
        pytest.param("not json at all", id="invalid-json"),
        pytest.param(RuntimeError("Gemini unavailable"), id="gemini-error"),
    ],
)
async def test_parse_jd_ai_failure(
    client: AsyncClient,
    fake_gemini: FakeGemini,
    snapshot: SnapshotAssertion,
    gemini_response: str | Exception,
) -> None:
    fake_gemini.respond_with(gemini_response)
    resp = await client.post(f"{V2}/campaigns/parse-jd", json={"job_description": JD})
    assert response_data(resp) == snapshot


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="missing"),
        pytest.param({"job_description": "   "}, id="blank"),
    ],
)
async def test_parse_jd_invalid(
    client: AsyncClient, fake_gemini: FakeGemini, snapshot: SnapshotAssertion, payload: object
) -> None:
    resp = await client.post(f"{V2}/campaigns/parse-jd", json=payload)
    assert response_data(resp) == snapshot
    assert fake_gemini.calls == []
