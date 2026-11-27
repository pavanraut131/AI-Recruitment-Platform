"""The operator commands (``app.modules.identity.cli``, spec §15 Q7) and the development
sign-in accounts of the seed (``app.devtools.seed_users``, spec §11.4)."""

from __future__ import annotations

import io

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.devtools import seed_users
from app.modules.identity import cli, passwords, service
from app.modules.identity.models import Agency, Membership, User
from app.platform.config import settings
from tests import postgres
from tests.default_agency import DEFAULT_AGENCY_ID

PASSWORD = "operator chosen password"


async def _memberships(db: AsyncSession, email: str) -> set[tuple[str, str]]:
    rows = await db.execute(
        select(Agency.slug, Membership.role)
        .join(Membership, Membership.agency_id == Agency.id)
        .join(User, User.id == Membership.user_id)
        .where(User.email == email)
    )
    return {(slug, role) for slug, role in rows.all()}


async def test_create_agency_with_a_new_owner(db_session: AsyncSession) -> None:
    lines = await cli.create_agency(
        db_session,
        name="Northbank Talent",
        owner_email="Owner@Northbank.example",
        owner_name="Nina",
        slug=None,
        password=PASSWORD,
    )
    assert lines[0].startswith("created agency 'Northbank Talent' (slug northbank-talent")
    assert lines[1] == "created user owner@northbank.example is its owner"
    assert await _memberships(db_session, "owner@northbank.example") == {
        ("northbank-talent", "owner")
    }
    user = (
        await db_session.execute(select(User).where(User.email == "owner@northbank.example"))
    ).scalar_one()
    assert user.display_name == "Nina" and user.email_verified_at is not None
    assert user.password_hash and passwords.verify_password_sync(user.password_hash, PASSWORD)

    # Same name again: a unique slug; an existing user keeps their password.
    lines = await cli.create_agency(
        db_session,
        name="Northbank Talent",
        owner_email="owner@northbank.example",
        owner_name=None,
        slug=None,
        password=None,
    )
    assert "slug northbank-talent-2" in lines[0]
    assert lines[1] == "existing user owner@northbank.example is its owner"


async def test_create_agency_refusals(db_session: AsyncSession) -> None:
    with pytest.raises(service.OperatorError, match="a password is needed"):
        await cli.create_agency(
            db_session,
            name="A",
            owner_email="new@a.example",
            owner_name=None,
            slug=None,
            password=None,
        )
    with pytest.raises(service.OperatorError, match="Use at least 12 characters"):
        await cli.create_agency(
            db_session,
            name="B",
            owner_email="new@b.example",
            owner_name=None,
            slug=None,
            password="short",
        )
    with pytest.raises(service.OperatorError, match="not an email address"):
        await cli.create_agency(
            db_session,
            name="C",
            owner_email="nope",
            owner_name=None,
            slug=None,
            password=PASSWORD,
        )
    with pytest.raises(service.OperatorError, match="an agency with the slug 'default' exists"):
        await cli.create_agency(
            db_session,
            name="D",
            owner_email="d@d.example",
            owner_name=None,
            slug="default",
            password=PASSWORD,
        )


async def test_create_owner_for_the_default_agency_and_rename_it(
    db_session: AsyncSession,
) -> None:
    lines = await cli.create_owner(
        db_session,
        email="you@agency.example",
        name="You",
        agency_name="Rao Recruiting",
        password=PASSWORD,
    )
    assert lines == [
        "default agency renamed to 'Rao Recruiting'",
        "created user you@agency.example is now an owner of the default agency",
    ]
    agency = await db_session.get(Agency, DEFAULT_AGENCY_ID)
    assert agency is not None and agency.name == "Rao Recruiting" and agency.slug == "default"
    assert await _memberships(db_session, "you@agency.example") == {("default", "owner")}
    again = await cli.create_owner(
        db_session, email="you@agency.example", name=None, agency_name=None, password=None
    )
    assert again == ["existing user you@agency.example is an owner of the default agency"]


async def test_create_owner_finds_the_default_agency_by_its_slug(db_session: AsyncSession) -> None:
    """Since E2-12 the code has no fixed agency id: ``create-owner`` acts on the agency with the
    slug ``default`` (the E2-01 migration's), and says so when there is none."""
    await db_session.execute(text("UPDATE agencies SET slug = 'renamed' WHERE slug = 'default'"))
    with pytest.raises(service.OperatorError, match="no agency with the slug 'default'"):
        await cli.create_owner(
            db_session, email="x@agency.example", name=None, agency_name=None, password=PASSWORD
        )


def test_the_cli_end_to_end(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main`` against a throwaway database: its own engine and transaction, the password from
    stdin, exit codes."""
    with postgres.throwaway_database("e205_cli") as engine:
        # The CLI connects as the owner role (MIGRATION_DATABASE_URL), like the other operator
        # commands (E2-04); the throwaway database's engine is the owner's.
        url = engine.url.render_as_string(hide_password=False)
        monkeypatch.setattr(settings, "MIGRATION_DATABASE_URL", url)
        monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
        assert (
            cli.main(
                [
                    "create-owner",
                    "--email",
                    "boss@agency.example",
                    "--agency-name",
                    "Boss & Co",
                    "--password-stdin",
                ]
            )
            == 0
        )
        monkeypatch.setattr("sys.stdin", io.StringIO("too short\n"))
        assert (
            cli.main(
                ["create-agency", "--name", "X", "--owner-email", "x@x.example", "--password-stdin"]
            )
            == 1
        )
        out = capsys.readouterr()
        assert "identity: default agency renamed to 'Boss & Co'" in out.out
        assert "identity: password: Use at least 12 characters" in out.err
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT name FROM agencies ORDER BY name")
            ).scalars().all() == ["Boss & Co"]  # the failed command left nothing behind
            assert conn.execute(text("SELECT email FROM users")).scalars().all() == [
                "boss@agency.example"
            ]


# ── The seed's accounts ───────────────────────────────────────
async def test_seed_users_two_agencies_one_user_per_role(db_session: AsyncSession) -> None:
    seeded = await seed_users.seed_users(db_session)
    assert [(u.email, u.role.value) for u in seeded] == [
        ("owner@default.example", "owner"),
        ("admin@default.example", "admin"),
        ("recruiter@default.example", "recruiter"),
        ("viewer@default.example", "viewer"),
        ("owner@northbank.example", "owner"),
        ("admin@northbank.example", "admin"),
        ("recruiter@northbank.example", "recruiter"),
        ("viewer@northbank.example", "viewer"),
    ]
    assert all(u.created for u in seeded)
    assert await _memberships(db_session, "viewer@northbank.example") == {("northbank", "viewer")}
    user = (
        await db_session.execute(select(User).where(User.email == "admin@default.example"))
    ).scalar_one()
    assert user.password_hash and passwords.verify_password_sync(
        user.password_hash, seed_users.DEV_PASSWORD
    )
    # Idempotent: a second run creates nothing.
    assert not any(u.created for u in await seed_users.seed_users(db_session))
    assert any("hireflow-dev-password" in line for line in seed_users.describe(seeded))


async def test_seed_users_refused_outside_development(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "staging")
    with pytest.raises(seed_users.SeedUsersRefused):
        await seed_users.seed_users(db_session)


async def test_the_seeded_agencies_have_a_client(
    db_session: AsyncSession, db_connection: AsyncConnection
) -> None:
    """E2-06: an agency without any client gets "General" (``hiring.ensure_first_client``, what
    ``seed_users.run`` does for each seeded agency); one that has a client keeps what it has."""
    from app.modules.hiring import service as hiring
    from app.modules.hiring.models import Client
    from app.platform.tenancy import bind

    seeded = await seed_users.seed_users(db_session)
    northbank = next(u.agency_id for u in seeded if u.agency_name == seed_users.SECOND_AGENCY_NAME)
    async with AsyncSession(
        bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    ) as db:
        bind(db, northbank)
        assert await hiring.ensure_first_client(db) is True
        assert await hiring.ensure_first_client(db) is False  # idempotent
        names = (await db.scalars(select(Client.name))).all()
        assert names == ["General"]
    assert await hiring.ensure_first_client(db_session) is False  # the default agency's General
