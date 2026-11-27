"""Authorization (EPIC-02 spec §5.2, §5.4, §8, §11.3): the permission matrix and its
enforcement on every ``/api/v2`` operation.

- **The spec and the code agree.** The §5.2 table in ``docs/specs/EPIC-02-identity-tenancy.md``
  is parsed and compared with ``ROLE_PERMISSIONS`` cell by cell (✅, ✅ scoped, —), so neither can
  change without the other.
- **Every operation declares the permission the spec gives it** (§8.2, §8.3): :data:`EXPECTED`
  names one for every operation of the OpenAPI document, and an operation that is missing here
  fails the test, so a new endpoint is decided here when it is added.
- **The matrix, for real:** every role, signed in with real cookies, calls every operation that
  needs a permission; the answer is ``403`` exactly when ``ROLE_PERMISSIONS`` says the role
  lacks it. Requests use unknown ids and empty bodies, so an allowed call ends in a 404 or 422
  (or a 200 for a list) and changes nothing.
- ``Me.permissions``, the in-handler check of the bulk ``delete`` action, and the dependency's
  identity (one callable per permission).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity import permissions
from app.modules.identity.dependencies import (
    any_member,
    permission_of,
    public,
    require_permission,
)
from app.modules.identity.permissions import (
    CLIENT_SCOPED_ROLES,
    ROLE_PERMISSIONS,
    Permission,
    denied_detail,
    permissions_of,
)
from app.modules.identity.schemas import Role
from app.platform.tenancy import DEFAULT_AGENCY_ID
from tests.identity.conftest import V2, Users, csrf, problem

P = Permission
SPEC = Path(__file__).resolve().parents[3] / "docs" / "specs" / "EPIC-02-identity-tenancy.md"
UNKNOWN = "00000000-0000-4000-8000-00000000dead"

PUBLIC = "public"
ANY_MEMBER = "any member"

# Spec §8.2 (new endpoints) and §8.3 (existing ones: reads need workspace:read; the writes as
# listed there).
EXPECTED: dict[str, Permission | str] = {
    # auth and the signed-in user
    "getCsrfToken": PUBLIC,
    "login": PUBLIC,
    "requestPasswordReset": PUBLIC,  # E2-07
    "confirmPasswordReset": PUBLIC,
    "previewInvitation": PUBLIC,
    "acceptInvitation": PUBLIC,  # anonymous, or with the invitee's own session
    "logout": ANY_MEMBER,
    "getMe": ANY_MEMBER,
    "updateMe": ANY_MEMBER,
    "changePassword": ANY_MEMBER,
    "setActiveAgency": ANY_MEMBER,
    # the agency
    "getAgency": P.workspace_read,
    "updateAgency": P.agency_manage,
    # the team (E2-07); an owner or admin as the target also needs team:manage_privileged
    "listMembers": P.team_read,
    "listInvitations": P.team_read,
    "updateMember": P.team_manage,
    "deactivateMember": P.team_manage,
    "reactivateMember": P.team_manage,
    "createInvitation": P.team_manage,
    "resendInvitation": P.team_manage,
    "revokeInvitation": P.team_manage,
    # clients
    "listClients": P.client_read,
    "getClient": P.client_read,
    "createClient": P.client_manage,
    "updateClient": P.client_manage,
    # campaigns
    "listCampaigns": P.workspace_read,
    "getCampaign": P.workspace_read,
    "getCampaignStats": P.workspace_read,
    "createCampaign": P.campaign_write,
    "updateCampaign": P.campaign_write,
    "parseJobDescription": P.campaign_write,
    "deleteCampaign": P.campaign_delete,
    # uploads and processing
    "uploadResumes": P.resume_upload,
    "retryFailedResumes": P.resume_upload,
    "listCampaignResumes": P.workspace_read,
    "streamCampaignEvents": P.workspace_read,
    # applications
    "listCampaignApplications": P.workspace_read,
    "getApplication": P.workspace_read,
    "updateApplication": P.application_review,
    "bulkApplicationAction": P.application_review,  # + application:delete for "delete"
    "deleteApplication": P.application_delete,
    "exportCampaignApplications": P.application_export,
    # people and the dashboard
    "listPeople": P.workspace_read,
    "getPerson": P.workspace_read,
    "getStats": P.workspace_read,
    "getHome": P.workspace_read,
}


def _routes() -> dict[str, tuple[str, str, Any]]:
    """``operationId -> (method, path, route)`` for every v2 operation."""
    from fastapi.routing import APIRoute, iter_route_contexts

    from app.v2 import v2_app

    found: dict[str, tuple[str, str, Any]] = {}
    for route in iter_route_contexts(v2_app.routes):
        if isinstance(route.original_route, APIRoute):
            for method in route.methods or ():
                found[route.operation_id] = (method, route.path_format, route)
    return found


def _access(route: Any) -> Permission | str:
    from app.v2 import route_access

    access = route_access(route)
    if access is public:
        return PUBLIC
    if access is any_member:
        return ANY_MEMBER
    assert isinstance(access, frozenset) and len(access) == 1, access
    (permission,) = access
    return permission


# ── The spec, the matrix and the routes agree ─────────────────
def _spec_table() -> tuple[list[str], dict[str, dict[str, str]]]:
    """The §5.2 table: the role columns, and ``permission -> role -> cell``."""
    text = SPEC.read_text(encoding="utf-8")
    section = text.split("### 5.2 Permission matrix", 1)[1].split("**Guard rails**", 1)[0]
    lines = [line.strip() for line in section.splitlines() if line.strip().startswith("|")]

    def cells(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip("|").split("|")]

    header = cells(lines[0])
    assert header[:2] == ["Permission", "Allows"], header
    roles = header[2:]
    table: dict[str, dict[str, str]] = {}
    for line in lines[2:]:  # after the header and its separator
        row = cells(line)
        name = re.fullmatch(r"`([a-z_]+:[a-z_]+)`", row[0])
        if name is None:  # the "(any member)" row
            assert "any member" in row[0], row
            continue
        table[name.group(1)] = dict(zip(roles, row[2:], strict=True))
    return roles, table


def test_the_spec_matrix_matches_role_permissions() -> None:
    roles, table = _spec_table()
    assert roles == [role.value for role in Role]
    assert list(table) == [p.value for p in Permission]  # every permission, in the same order
    for permission in Permission:
        for role in Role:
            granted = permission in ROLE_PERMISSIONS[role]
            expected = ("✅ scoped" if role in CLIENT_SCOPED_ROLES else "✅") if granted else "—"
            assert table[permission.value][role.value] == expected, (permission, role)


def test_the_spec_decisions_on_the_defaults() -> None:
    """§15 Q4: recruiters may delete and export in their scope, viewers may not export,
    recruiters do not see the team; only owners manage owners, admins and the agency."""
    recruiter, viewer = ROLE_PERMISSIONS[Role.recruiter], ROLE_PERMISSIONS[Role.viewer]
    assert {P.application_delete, P.application_export} <= recruiter
    assert P.application_export not in viewer
    assert P.team_read not in recruiter
    for permission in (P.team_manage_privileged, P.agency_manage):
        assert [r for r in Role if permission in ROLE_PERMISSIONS[r]] == [Role.owner]
    # Viewers only read; nobody has a permission the owner lacks.
    assert viewer == {P.workspace_read, P.client_read}
    assert all(ROLE_PERMISSIONS[r] <= ROLE_PERMISSIONS[Role.owner] for r in Role)


def test_every_operation_declares_the_permission_of_the_spec() -> None:
    routes = _routes()
    assert sorted(routes) == sorted(EXPECTED), "a v2 operation without an EXPECTED entry"
    declared = {operation_id: _access(route) for operation_id, (_, _, route) in routes.items()}
    assert declared == EXPECTED


def test_the_document_lists_each_operations_permissions() -> None:
    from app.v2 import v2_app

    document = v2_app.openapi()
    for path, operations in document["paths"].items():
        for method, operation in operations.items():
            expected = EXPECTED[operation["operationId"]]
            if isinstance(expected, Permission):
                assert operation["x-permissions"] == [expected.value], (method, path)
                assert "403" in operation["responses"], (method, path)
            else:
                assert "x-permissions" not in operation, (method, path)


def test_one_dependency_per_permission() -> None:
    for permission in Permission:
        dependency = require_permission(permission)
        assert require_permission(permission) is dependency
        assert permission_of(dependency) is permission
    assert permission_of(public) is None


# ── The matrix, signed in for real ────────────────────────────
def _calls() -> list[tuple[str, str, str, Permission]]:
    """``(operationId, method, url, permission)`` for every operation that needs one, with
    unknown ids in the path."""
    return [
        (operation_id, method, V2 + re.sub(r"\{[^}]+\}", UNKNOWN, path), permission)
        for operation_id, (method, path, _) in sorted(_routes().items())
        if isinstance(permission := EXPECTED[operation_id], Permission)
    ]


@pytest.mark.parametrize("role", list(Role), ids=lambda role: role.value)
async def test_each_role_gets_exactly_its_permissions(
    users: Users, auth_client: AsyncClient, role: Role
) -> None:
    email = f"{role.value}@matrix.example"
    await users.user(email, {DEFAULT_AGENCY_ID: role})
    assert (await users.login(email)).status_code == 200

    calls = _calls()
    assert len(calls) == 35  # every operation but the 11 public and any-member ones
    wrong: list[str] = []
    for operation_id, method, url, permission in calls:
        kwargs: dict[str, Any] = (
            {} if method == "GET" else {"json": {}, "headers": csrf(auth_client)}
        )
        resp = await auth_client.request(method, url, **kwargs)
        allowed = permission in ROLE_PERMISSIONS[role]
        if allowed and resp.status_code in (401, 403):
            wrong.append(f"{operation_id}: {resp.status_code} for an allowed {permission.value}")
        elif not allowed and (
            resp.status_code != 403 or problem(resp)["detail"] != denied_detail(permission)
        ):
            wrong.append(f"{operation_id}: {resp.status_code} {resp.text} without {permission}")
    assert wrong == []


@pytest.mark.parametrize("role", list(Role), ids=lambda role: role.value)
async def test_me_lists_the_roles_permissions(
    users: Users, auth_client: AsyncClient, role: Role
) -> None:
    email = f"{role.value}@me.example"
    await users.user(email, {DEFAULT_AGENCY_ID: role})
    me = (await users.login(email)).json()
    assert me["role"] == role.value
    assert me["permissions"] == [p.value for p in permissions_of(role)]
    assert (await auth_client.get(f"{V2}/me")).json()["permissions"] == me["permissions"]


async def test_me_has_no_permissions_without_an_active_agency(
    users: Users, auth_client: AsyncClient
) -> None:
    northbank = await users.agency()
    await users.user(
        "two@agencies.example", {DEFAULT_AGENCY_ID: Role.owner, northbank: Role.viewer}
    )
    me = (await users.login("two@agencies.example")).json()
    assert (me["active_agency"], me["role"], me["permissions"]) == (None, None, [])


async def test_bulk_delete_also_needs_application_delete(
    users: Users,
    auth_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``bulkApplicationAction`` needs ``application:review``; its ``delete`` action also needs
    ``application:delete`` (§8.3). The matrix gives both to the same roles, so a role with one
    and not the other is made up here."""
    from tests.factories import make_candidate

    application = await make_candidate(db_session)
    without_delete = dict(ROLE_PERMISSIONS)
    without_delete[Role.recruiter] = ROLE_PERMISSIONS[Role.recruiter] - {P.application_delete}
    monkeypatch.setattr(permissions, "ROLE_PERMISSIONS", without_delete)
    await users.user("rec@bulk.example", {DEFAULT_AGENCY_ID: Role.recruiter})
    assert (await users.login("rec@bulk.example")).status_code == 200

    url = f"{V2}/campaigns/{application.campaign_id}/applications/bulk"
    ids = [str(application.id)]
    deleted = await auth_client.post(
        url, json={"action": "delete", "ids": ids}, headers=csrf(auth_client)
    )
    assert deleted.status_code == 403
    assert problem(deleted)["detail"] == "You don't have permission to delete applications"
    staged = await auth_client.post(
        url, json={"action": "stage", "ids": ids, "stage": "screened"}, headers=csrf(auth_client)
    )
    assert staged.status_code == 200, staged.text
    assert staged.json()["affected"] == ids
