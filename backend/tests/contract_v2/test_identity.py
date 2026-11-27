"""Contract: the identity endpoints of /api/v2 (E2-05): /auth (csrf, login, logout), /me
(profile, password, active agency) and /agency, with real sessions and cookies.

Random values are masked before the snapshot on top of the usual UUID/datetime masking: the CSRF
token (``<csrf-token>``), cookie values (``<value>``), a deleted cookie's ``expires`` date
(``<date>``) and ``Retry-After`` (``<seconds>``), so the
snapshots pin the shapes, statuses, problem details and cookie attributes."""

from __future__ import annotations

import re
from typing import Any

import httpx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from syrupy.assertion import SnapshotAssertion

from app.modules.identity.schemas import Role
from app.platform.tenancy import DEFAULT_AGENCY_ID
from tests.characterization.snapshot_utils import response_data
from tests.identity.conftest import PASSWORD, V2, Users, csrf

COOKIE_VALUE = re.compile(r"^([^=]+)=([^;]*)")
EXPIRES = re.compile(r"expires=[^;]+")


def _mask_cookie(match: re.Match[str]) -> str:
    name, value = match.groups()
    kept = value if value in ("", '""') else "<value>"  # an empty value: the cookie is deleted
    return f"{name}={kept}"


def _data(resp: httpx.Response, *headers: str) -> dict[str, Any]:
    data = response_data(resp, *headers)
    body = data["body"]
    if isinstance(body, dict) and "csrf_token" in body:
        body["csrf_token"] = "<csrf-token>"
    cookies = [
        EXPIRES.sub("expires=<date>", COOKIE_VALUE.sub(_mask_cookie, c))
        for c in resp.headers.get_list("set-cookie")
    ]
    if cookies:
        data["set-cookie"] = sorted(cookies)
    if "retry-after" in data["headers"] and data["headers"]["retry-after"]:
        data["headers"]["retry-after"] = "<seconds>"
    return data


async def test_auth(users: Users, auth_client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    await users.user("ada@example.com")
    responses: dict[str, Any] = {}

    csrf_resp = await auth_client.get(f"{V2}/auth/csrf")
    responses["csrf"] = _data(csrf_resp)
    token = csrf_resp.json()["csrf_token"]
    login = {"email": "ada@example.com", "password": PASSWORD}

    responses["login_without_csrf"] = _data(await auth_client.post(f"{V2}/auth/login", json=login))
    responses["login_invalid_body"] = _data(
        await auth_client.post(
            f"{V2}/auth/login", json={"email": "ada@example.com"}, headers={"X-CSRF-Token": token}
        )
    )
    responses["login_wrong_password"] = _data(
        await auth_client.post(
            f"{V2}/auth/login",
            json={**login, "password": "wrong password!!"},
            headers={"X-CSRF-Token": token},
        )
    )
    responses["login"] = _data(
        await auth_client.post(f"{V2}/auth/login", json=login, headers={"X-CSRF-Token": token})
    )
    responses["logout"] = _data(
        await auth_client.post(f"{V2}/auth/logout", headers=csrf(auth_client))
    )
    responses["logout_again"] = _data(
        await auth_client.post(f"{V2}/auth/logout", headers={"X-CSRF-Token": token})
    )
    assert responses == snapshot


async def test_login_failures(
    users: Users, auth_client: AsyncClient, snapshot: SnapshotAssertion
) -> None:
    northbank = await users.agency()
    await users.user("nina@example.com", {northbank: Role.viewer})
    from sqlalchemy import text

    await users.db.execute(
        text("UPDATE agencies SET status = 'suspended' WHERE id = :id"), {"id": northbank}
    )
    await users.db.flush()
    responses = {"no_agency_access": _data(await users.login("nina@example.com"))}
    for _ in range(5):
        await users.login("nobody@example.com", "wrong password!!")
    responses["rate_limited"] = _data(await users.login("nobody@example.com"), "retry-after")
    assert responses == snapshot


async def test_me(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession, snapshot: SnapshotAssertion
) -> None:
    northbank = await users.agency("Northbank Talent")
    await users.user("ada@example.com", {DEFAULT_AGENCY_ID: Role.owner, northbank: Role.recruiter})
    responses: dict[str, Any] = {"get_me_signed_out": _data(await auth_client.get(f"{V2}/me"))}
    await users.login("ada@example.com")
    responses["get_me_without_agency"] = _data(await auth_client.get(f"{V2}/me"))
    responses["agency_without_agency"] = _data(await auth_client.get(f"{V2}/agency"))
    responses["set_active_agency"] = _data(
        await auth_client.put(
            f"{V2}/me/active-agency", json={"agency_id": str(northbank)}, headers=csrf(auth_client)
        )
    )
    responses["set_active_agency_unknown"] = _data(
        await auth_client.put(
            f"{V2}/me/active-agency",
            json={"agency_id": "00000000-0000-0000-0000-000000000000"},
            headers=csrf(auth_client),
        )
    )
    responses["update_me"] = _data(
        await auth_client.patch(
            f"{V2}/me", json={"display_name": "Ada Lovelace"}, headers=csrf(auth_client)
        )
    )
    responses["update_me_invalid"] = _data(
        await auth_client.patch(f"{V2}/me", json={"display_name": ""}, headers=csrf(auth_client))
    )
    responses["change_password_wrong_current"] = _data(
        await auth_client.post(
            f"{V2}/me/password",
            json={"current_password": "not my password", "new_password": "new passphrase here"},
            headers=csrf(auth_client),
        )
    )
    responses["change_password"] = _data(
        await auth_client.post(
            f"{V2}/me/password",
            json={"current_password": PASSWORD, "new_password": "new passphrase here"},
            headers=csrf(auth_client),
        )
    )
    responses["get_me"] = _data(await auth_client.get(f"{V2}/me"))
    assert responses == snapshot


async def test_agency(users: Users, auth_client: AsyncClient, snapshot: SnapshotAssertion) -> None:
    await users.user("ada@example.com")
    await users.user("rob@example.com", {DEFAULT_AGENCY_ID: Role.recruiter})
    await users.login("rob@example.com")
    responses = {
        "get_agency": _data(await auth_client.get(f"{V2}/agency")),
        "update_agency_not_owner": _data(
            await auth_client.patch(f"{V2}/agency", json={"name": "X"}, headers=csrf(auth_client))
        ),
    }
    await users.login("ada@example.com")
    responses["update_agency"] = _data(
        await auth_client.patch(
            f"{V2}/agency", json={"name": "Rao Recruiting"}, headers=csrf(auth_client)
        )
    )
    responses["update_agency_invalid"] = _data(
        await auth_client.patch(f"{V2}/agency", json={"name": " "}, headers=csrf(auth_client))
    )
    # Expired session: 401 and the cookie is cleared.
    from sqlalchemy import update

    from app.modules.identity.models import UserSession

    await users.db.execute(update(UserSession).values(revoked_at=UserSession.created_at))
    await users.db.flush()
    responses["session_ended"] = _data(await auth_client.get(f"{V2}/campaigns"))
    responses["not_signed_in"] = _data(await auth_client.get(f"{V2}/campaigns"))
    assert responses == snapshot
