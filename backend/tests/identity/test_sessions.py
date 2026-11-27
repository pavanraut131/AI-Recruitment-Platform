"""Session lifetime and live checks (spec §4.1, §4.3 "Expiry and rotation"): idle and absolute
expiry, revocation, throttled ``last_seen_at`` writes, user and membership status read on every
request, and the one-query principal lookup."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity import service
from app.modules.identity.models import Membership, User, UserSession
from tests.contract_v2.data import counting_statements
from tests.identity.conftest import SESSION_COOKIE, V2, Users, problem, set_cookie_headers


async def _signed_in(users: Users, auth_client: AsyncClient) -> tuple[uuid.UUID, bytes]:
    user_id = await users.user("ada@example.com")
    assert (await users.login("ada@example.com")).status_code == 200
    token = auth_client.cookies.get(SESSION_COOKIE)
    assert token is not None
    return user_id, hashlib.sha256(token.encode()).digest()


async def _session(db: AsyncSession, id_hash: bytes) -> UserSession:
    db.expire_all()
    session = await db.get(UserSession, id_hash)
    assert session is not None
    return session


async def _set(db: AsyncSession, id_hash: bytes, **values: object) -> None:
    await db.execute(update(UserSession).where(UserSession.id_hash == id_hash).values(**values))
    await db.flush()


@pytest.mark.parametrize(
    "expired",
    [
        {"idle_expires_at": datetime.now(UTC) - timedelta(seconds=1)},
        {"absolute_expires_at": datetime.now(UTC) - timedelta(seconds=1)},
        {"revoked_at": datetime.now(UTC)},
    ],
    ids=["idle", "absolute", "revoked"],
)
async def test_an_ended_session_is_401_and_clears_the_cookie(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession, expired: dict[str, object]
) -> None:
    _, id_hash = await _signed_in(users, auth_client)
    token = auth_client.cookies.get(SESSION_COOKIE) or ""
    await _set(db_session, id_hash, **expired)
    for path in ("/me", "/campaigns", "/agency"):
        auth_client.cookies.clear()  # the previous 401 deleted it
        auth_client.cookies.set(SESSION_COOKIE, token, domain="test.local")
        resp = await auth_client.get(V2 + path)
        assert resp.status_code == 401, path
        assert problem(resp)["detail"] == "Your session has ended. Sign in again."
        assert "max-age=0" in set_cookie_headers(resp)[SESSION_COOKIE].lower()


async def test_a_disabled_user_is_signed_out_on_the_next_request(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id, _ = await _signed_in(users, auth_client)
    await db_session.execute(update(User).where(User.id == user_id).values(status="disabled"))
    await db_session.flush()
    assert (await auth_client.get(f"{V2}/me")).status_code == 401


async def test_membership_and_agency_status_are_read_live(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id, _ = await _signed_in(users, auth_client)
    assert (await auth_client.get(f"{V2}/campaigns")).status_code == 200
    await db_session.execute(
        update(Membership).where(Membership.user_id == user_id).values(status="deactivated")
    )
    await db_session.flush()
    resp = await auth_client.get(f"{V2}/campaigns")
    assert resp.status_code == 403
    assert problem(resp)["detail"] == "Choose an agency"
    me = (await auth_client.get(f"{V2}/me")).json()  # /me still answers: no agency
    assert me["active_agency"] is None and me["memberships"] == [] and me["role"] is None


async def test_activity_moves_the_idle_deadline_at_most_every_five_minutes(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, id_hash = await _signed_in(users, auth_client)
    start = await _session(db_session, id_hash)
    # A request soon after: no write.
    assert (await auth_client.get(f"{V2}/me")).status_code == 200
    same = await _session(db_session, id_hash)
    assert (same.last_seen_at, same.idle_expires_at) == (start.last_seen_at, start.idle_expires_at)

    # Last seen six minutes ago (idle deadline as it was then): the next request writes.
    earlier = start.last_seen_at - timedelta(minutes=6)
    await _set(
        db_session, id_hash, last_seen_at=earlier, idle_expires_at=earlier + timedelta(hours=8)
    )
    assert (await auth_client.get(f"{V2}/me")).status_code == 200
    moved = await _session(db_session, id_hash)
    assert moved.last_seen_at > earlier
    assert moved.idle_expires_at > earlier + timedelta(hours=8)
    assert moved.idle_expires_at <= moved.absolute_expires_at


async def test_the_idle_deadline_never_passes_the_absolute_one(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, id_hash = await _signed_in(users, auth_client)
    now = datetime.now(UTC)
    await _set(
        db_session,
        id_hash,
        last_seen_at=now - timedelta(minutes=10),
        absolute_expires_at=now + timedelta(minutes=30),
    )
    assert (await auth_client.get(f"{V2}/me")).status_code == 200
    session = await _session(db_session, id_hash)
    assert session.idle_expires_at == session.absolute_expires_at


async def test_authenticate_reads_the_session_in_one_query(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _signed_in(users, auth_client)
    token = auth_client.cookies.get(SESSION_COOKIE)
    assert token is not None
    with counting_statements(db_session) as statements:
        signed_in = await service.authenticate(db_session, token)
    assert signed_in is not None and signed_in.membership is not None
    assert len([s for s in statements if not s.lstrip().upper().startswith("SAVEPOINT")]) == 1


async def test_a_signed_in_request_costs_one_query_more(
    users: Users,
    auth_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §11.3: at most one extra query per request (the principal lookup), besides the
    transaction bookkeeping (the lookup's commit, and the tenant setting of the next
    transaction). The baseline is the same request without a session (AUTH_REQUIRED=false)."""
    from app.platform.config import settings

    await _signed_in(users, auth_client)
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)

    def queries(statements: list[str]) -> list[str]:
        return [
            s
            for s in statements
            if not s.lstrip().upper().startswith(("SAVEPOINT", "RELEASE", "ROLLBACK"))
            and "set_config" not in s
        ]

    async with AsyncClient(transport=auth_client._transport, base_url="http://test") as anon:
        with counting_statements(db_session) as anonymous:
            assert (await anon.get(f"{V2}/stats")).status_code == 200
    with counting_statements(db_session) as signed_in:
        assert (await auth_client.get(f"{V2}/stats")).status_code == 200
    assert len(queries(signed_in)) == len(queries(anonymous)) + 1


async def test_sign_in_picks_the_only_or_the_last_used_agency(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.modules.identity.schemas import Role
    from tests.default_agency import DEFAULT_AGENCY_ID

    northbank = await users.agency()
    user_id = await users.user(
        "ada@example.com", {DEFAULT_AGENCY_ID: Role.admin, northbank: Role.viewer}
    )
    # Never used either: no active agency yet (the frontend shows the picker).
    me = (await users.login("ada@example.com")).json()
    assert me["active_agency"] is None and len(me["memberships"]) == 2
    # Used Northbank last: sign-in goes there.
    await db_session.execute(
        update(Membership)
        .where(Membership.user_id == user_id, Membership.agency_id == northbank)
        .values(last_active_at=datetime.now(UTC))
    )
    await db_session.flush()
    me = (await users.login("ada@example.com")).json()
    assert me["active_agency"]["id"] == str(northbank) and me["role"] == "viewer"
    last_login = (
        await db_session.execute(select(User.last_login_at).where(User.id == user_id))
    ).scalar_one()
    assert last_login is not None
