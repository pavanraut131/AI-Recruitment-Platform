"""Operator commands for agencies and their owners (EPIC-02 spec §2, §15 Q7). There is no
self-serve sign-up: an operator creates an agency and its first owner, who invites the team
(E2-07).

Usage (from ``backend/``, or inside the backend container)::

    python -m app.modules.identity.cli create-agency --name "Northbank Talent" \\
        --owner-email owner@northbank.example [--owner-name "Asha Rao"] [--slug northbank]

    python -m app.modules.identity.cli create-owner --email you@agency.example \\
        [--name "Your Name"] [--agency-name "Your Agency"]

``create-agency`` creates an agency and makes the user its owner. ``create-owner`` makes the
user an owner of the **default agency** (the one every pre-E2 row belongs to,
``app.platform.tenancy.DEFAULT_AGENCY_ID``) and, with ``--agency-name``, renames that agency
(it is created as "Default agency" by the E2-01 migration).

A user who does not exist yet is created, with a password read from the terminal (asked twice)
or, with ``--password-stdin``, from the first line of standard input (for scripts:
``printf '%s\\n' "$PASSWORD" | python -m … --password-stdin``). It must meet the password policy
(12 to 128 characters, not the email). An existing user keeps their password and gets the
membership. Each command is one transaction and prints what it did; it exits 1 with a message
when it cannot.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.identity import service
from app.modules.identity.schemas import Role
from app.platform.config import settings
from app.platform.tenancy import DEFAULT_AGENCY_ID


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("Password for the new user: ")
    if getpass.getpass("Repeat it: ") != first:
        raise service.OperatorError("the passwords do not match")
    return first


async def _password_if_new(db: AsyncSession, email: str, from_stdin: bool) -> str | None:
    """A password only when the account does not exist yet (asked lazily)."""
    if await service.user_exists(db, email):
        return None
    return _read_password(from_stdin)


async def create_agency(
    db: AsyncSession,
    *,
    name: str,
    owner_email: str,
    owner_name: str | None,
    slug: str | None,
    password: str | None,
) -> list[str]:
    agency = await service.create_agency(db, name, slug=slug)
    user = await service.ensure_user(db, owner_email, display_name=owner_name, password=password)
    await service.ensure_membership(db, agency.id, user.id, Role.owner)
    return [
        f"created agency {agency.name!r} (slug {agency.slug}, id {agency.id})",
        f"{'created user' if user.created else 'existing user'} {user.email} is its owner",
    ]


async def create_owner(
    db: AsyncSession,
    *,
    email: str,
    name: str | None,
    agency_name: str | None,
    password: str | None,
) -> list[str]:
    lines = []
    if agency_name:
        agency = await service.rename_agency(db, DEFAULT_AGENCY_ID, agency_name)
        lines.append(f"default agency renamed to {agency.name!r}")
    user = await service.ensure_user(db, email, display_name=name, password=password)
    created = await service.ensure_membership(db, DEFAULT_AGENCY_ID, user.id, Role.owner)
    lines.append(
        f"{'created user' if user.created else 'existing user'} {user.email} "
        f"{'is now an' if created else 'is an'} owner of the default agency"
    )
    return lines


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.modules.identity.cli",
        description="Create agencies and their owners.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    agency = commands.add_parser("create-agency", help="create an agency and its owner")
    agency.add_argument("--name", required=True, help="the agency's name")
    agency.add_argument("--owner-email", required=True)
    agency.add_argument("--owner-name", help="display name of a new owner (default: email)")
    agency.add_argument("--slug", help="URL-safe handle (default: from the name)")
    agency.add_argument("--password-stdin", action="store_true")

    owner = commands.add_parser("create-owner", help="add an owner to the default agency")
    owner.add_argument("--email", required=True)
    owner.add_argument("--name", help="display name of a new user (default: email)")
    owner.add_argument("--agency-name", help="rename the default agency")
    owner.add_argument("--password-stdin", action="store_true")
    return parser


async def run(args: argparse.Namespace) -> list[str]:
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            if args.command == "create-agency":
                password = await _password_if_new(db, args.owner_email, args.password_stdin)
                lines = await create_agency(
                    db,
                    name=args.name,
                    owner_email=args.owner_email,
                    owner_name=args.owner_name,
                    slug=args.slug,
                    password=password,
                )
            else:
                password = await _password_if_new(db, args.email, args.password_stdin)
                lines = await create_owner(
                    db,
                    email=args.email,
                    name=args.name,
                    agency_name=args.agency_name,
                    password=password,
                )
            await db.commit()
            return lines
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        lines = asyncio.run(run(args))
    except service.OperatorError as exc:
        print(f"identity: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(f"identity: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
