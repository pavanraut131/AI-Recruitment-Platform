"""Fixtures for the ``/api/v2`` contract tests. See README.md in this folder.

Same snapshot pattern as the v1 characterization tests: syrupy Amber snapshots through the same
masking serializer (``tests.snapshot_utils``), and the same database, fakes and
``client`` (``tests/conftest.py``; it overrides ``get_db`` in the mounted v2 app too).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sse_starlette.sse import AppStatus
from syrupy.assertion import SnapshotAssertion

# Real sign-in for the identity contract tests (test_identity.py): see tests/identity/conftest.py.
from tests.identity.conftest import auth_client, users  # noqa: E402, F401
from tests.snapshot_utils import MaskedAmberExtension

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """syrupy's ``snapshot`` with UUID/datetime masking (one masker per test)."""
    return snapshot.use_extension(MaskedAmberExtension)


@pytest_asyncio.fixture
async def raw_client(app: FastAPI, client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """Like ``client`` (same database session), but returns the 500 response of an unhandled
    exception instead of re-raising it."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _reset_sse_app_status() -> Iterator[None]:
    # sse-starlette keeps a process-wide anyio.Event bound to the first event loop that used it;
    # each test has its own loop.
    AppStatus.should_exit_event = None
    yield
    AppStatus.should_exit_event = None
