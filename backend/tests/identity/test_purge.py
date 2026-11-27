"""``identity.purge_expired`` (spec §4.3; E2-07): the daily task deletes sessions that ended more
than 30 days ago (idle or absolute expiry, or revocation), used or expired reset tokens, and
invitations that expired more than 30 days ago, and nothing else. It runs as the workers'
application role on an **unbound** session (identity's tables are global)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import Connection, create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.modules.identity import tasks
from app.modules.identity.models import Invitation, PasswordResetToken, User, UserSession
from app.modules.identity.service import hash_token, utcnow
from app.platform.config import settings
from app.platform.tenancy import DEFAULT_AGENCY_ID

DAY = timedelta(days=1)


@pytest.fixture
def connection() -> Iterator[Connection]:
    """The workers' role (SYNC_DATABASE_URL), in a transaction rolled back after the test."""
    engine = create_engine(settings.SYNC_DATABASE_URL, poolclass=NullPool)
    with engine.connect() as conn:
        outer = conn.begin()
        try:
            yield conn
        finally:
            outer.rollback()
    engine.dispose()


@pytest.fixture
def worker_session(connection: Connection, monkeypatch: pytest.MonkeyPatch) -> Session:
    """The task's sessions join the test's transaction (unbound, as in the worker)."""

    def session() -> Session:
        return Session(bind=connection, join_transaction_mode="create_savepoint")

    monkeypatch.setattr(tasks, "sync_session", session)
    return session()


def _session(user_id: uuid.UUID, name: str, **ends: timedelta | None) -> UserSession:
    now = utcnow()
    idle = ends.get("idle", DAY)
    absolute = ends.get("absolute", 7 * DAY)
    revoked = ends.get("revoked")
    assert idle is not None and absolute is not None
    return UserSession(
        id_hash=hash_token(name),
        user_id=user_id,
        created_at=now - 60 * DAY,
        last_seen_at=now - 60 * DAY,
        idle_expires_at=now + idle,
        absolute_expires_at=now + absolute,
        revoked_at=None if revoked is None else now + revoked,
    )


def _invitation(name: str, expires_in: timedelta) -> Invitation:
    return Invitation(
        agency_id=DEFAULT_AGENCY_ID,
        email=f"{name}@example.com",
        role="viewer",
        all_clients=True,
        client_ids=[],
        token_hash=hash_token(name),
        expires_at=utcnow() + expires_in,
        revoked_at=utcnow() - DAY if name == "revoked_recently" else None,
    )


def test_the_purge_deletes_what_has_outlived_its_use(worker_session: Session) -> None:
    db = worker_session
    user = User(email="ada@example.com", display_name="Ada", status="active")
    db.add(user)
    db.flush()
    now = utcnow()
    db.add_all(
        [
            # Sessions: kept while they ended less than 30 days ago.
            _session(user.id, "live"),
            _session(user.id, "idle_31_days_ago", idle=-31 * DAY),
            _session(user.id, "idle_29_days_ago", idle=-29 * DAY),
            _session(user.id, "absolute_40_days_ago", idle=-41 * DAY, absolute=-40 * DAY),
            _session(user.id, "revoked_31_days_ago", revoked=-31 * DAY),
            _session(user.id, "revoked_yesterday", revoked=-DAY),
            # Reset tokens: used or expired ones go at once.
            PasswordResetToken(
                user_id=user.id, token_hash=hash_token("r_live"), expires_at=now + DAY
            ),
            PasswordResetToken(
                user_id=user.id, token_hash=hash_token("r_expired"), expires_at=now - DAY
            ),
            PasswordResetToken(
                user_id=user.id,
                token_hash=hash_token("r_used"),
                expires_at=now + DAY,
                used_at=now,
            ),
            # Invitations: kept until 30 days after they expired, whatever their state.
            _invitation("pending", 7 * DAY),
            _invitation("expired_yesterday", -DAY),
            _invitation("revoked_recently", 6 * DAY),
            _invitation("expired_31_days_ago", -31 * DAY),
        ]
    )
    db.commit()

    counts = tasks.purge_expired()
    assert counts == {"sessions": 3, "password_reset_tokens": 2, "invitations": 1}

    db.expire_all()
    sessions = set(db.scalars(select(UserSession.id_hash)))
    assert sessions == {
        hash_token(name) for name in ("live", "idle_29_days_ago", "revoked_yesterday")
    }
    tokens = set(db.scalars(select(PasswordResetToken.token_hash)))
    assert tokens == {hash_token("r_live")}
    emails = set(db.scalars(select(Invitation.email)))
    assert emails == {
        "pending@example.com",
        "expired_yesterday@example.com",
        "revoked_recently@example.com",
    }
    assert db.scalar(select(User.id)) == user.id  # users are never purged
    assert tasks.purge_expired() == {"sessions": 0, "password_reset_tokens": 0, "invitations": 0}


def test_the_purge_runs_unbound_as_the_application_role(
    worker_session: Session, connection: Connection
) -> None:
    role = connection.execute(text("SELECT current_user")).scalar_one()
    assert role == "hireflow_app"
    assert not worker_session.info  # no tenant binding
    tasks.purge_expired()
    setting = connection.execute(text("SELECT current_setting('app.agency_id', true)")).scalar()
    assert not setting
