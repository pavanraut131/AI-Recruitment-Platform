"""Campaign CRUD and stats endpoints, against real Postgres (see ``tests/conftest.py``)."""

import pytest
from httpx import AsyncClient


# ── Health check ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── Campaign CRUD ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_create_campaign(client: AsyncClient):
    payload = {
        "title": "AI Engineer Batch 1",
        "role": "AI Engineer",
        "required_skills": ["Python", "FastAPI", "LLMs"],
    }
    resp = await client.post("/api/v1/campaigns", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "AI Engineer Batch 1"
    assert data["role"] == "AI Engineer"
    assert "Python" in data["required_skills"]
    assert "id" in data


@pytest.mark.asyncio
async def test_list_campaigns_empty(client: AsyncClient):
    resp = await client.get("/api/v1/campaigns")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_campaigns(client: AsyncClient):
    await client.post("/api/v1/campaigns", json={"title": "C1", "role": "Dev"})
    await client.post("/api/v1/campaigns", json={"title": "C2", "role": "Designer"})

    resp = await client.get("/api/v1/campaigns")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_get_campaign(client: AsyncClient):
    created = (
        await client.post("/api/v1/campaigns", json={"title": "Test", "role": "Engineer"})
    ).json()
    resp = await client.get(f"/api/v1/campaigns/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Test"


@pytest.mark.asyncio
async def test_get_campaign_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/campaigns/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_campaign(client: AsyncClient):
    created = (await client.post("/api/v1/campaigns", json={"title": "Old", "role": "Dev"})).json()
    resp = await client.put(f"/api/v1/campaigns/{created['id']}", json={"title": "New Title"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "New Title"


@pytest.mark.asyncio
async def test_delete_campaign(client: AsyncClient):
    created = (await client.post("/api/v1/campaigns", json={"title": "Del", "role": "Dev"})).json()
    resp = await client.delete(f"/api/v1/campaigns/{created['id']}")
    assert resp.status_code == 204

    # Confirm gone
    resp2 = await client.get(f"/api/v1/campaigns/{created['id']}")
    assert resp2.status_code == 404


# ── Campaign stats ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_campaign_stats(client: AsyncClient):
    created = (
        await client.post("/api/v1/campaigns", json={"title": "Stats", "role": "Dev"})
    ).json()
    resp = await client.get(f"/api/v1/campaigns/{created['id']}/stats")
    assert resp.status_code == 200
    stats = resp.json()
    assert stats["total_resumes"] == 0
    assert stats["strong_match"] == 0
