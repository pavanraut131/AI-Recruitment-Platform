import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.database import Base, get_db

# ── Use in-memory SQLite for tests ────────────────────────────
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False)


async def override_get_db():
    async with TestSession() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


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
    created = (await client.post("/api/v1/campaigns", json={"title": "Test", "role": "Engineer"})).json()
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
    created = (await client.post("/api/v1/campaigns", json={"title": "Stats", "role": "Dev"})).json()
    resp = await client.get(f"/api/v1/campaigns/{created['id']}/stats")
    assert resp.status_code == 200
    stats = resp.json()
    assert stats["total_resumes"] == 0
    assert stats["strong_match"] == 0
