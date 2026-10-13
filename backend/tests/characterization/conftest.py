"""Fixtures for the characterization (snapshot) tests. See README.md in this folder."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from syrupy.assertion import SnapshotAssertion

from tests.characterization.snapshot_utils import MaskedAmberExtension

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """syrupy's ``snapshot`` with UUID/datetime masking (one masker per test)."""
    return snapshot.use_extension(MaskedAmberExtension)


@pytest_asyncio.fixture
async def raw_client(app: FastAPI, client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """Like ``client`` (same database session), but returns the 500 response for unhandled
    exceptions instead of re-raising them, so the global error handler's output can be recorded.

    Use it only for the *last* request of a test: after a database error the shared session is
    unusable until the test's transaction is rolled back.
    """
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
