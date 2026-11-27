"""The permission matrix (EPIC-02 spec §5.2): what each agency role may do.

One place for it. :data:`ROLE_PERMISSIONS` maps every :class:`~app.modules.identity.schemas.Role`
to its :class:`Permission`\\ s; everything else is derived from it:

- the ``/api/v2`` routes declare the permission they need
  (``app.modules.identity.dependencies.require_permission``), and the v2 app refuses to start
  when a route declares none (``app.v2``);
- ``Me.permissions`` tells the frontend what to show (E2-10's ``usePermissions()``);
- the authorization tests are generated from it (``tests/identity/test_permissions.py``: every
  role against every operation), and a test checks the spec's §5.2 table against it, so the
  two cannot drift.

**Client scoping is not a permission.** A membership of a role in :data:`CLIENT_SCOPED_ROLES`
may be limited to some clients (``memberships.all_clients`` false); its permissions then apply
to those clients' campaigns only (the "scoped" cells of §5.2, spec §5.3). Owners and admins
always have every client (a database CHECK). The scope travels as an
``app.platform.tenancy.AccessScope``; services filter by it.

Pure data and functions: no FastAPI, no database. Imported by ``identity``'s own code; other
modules' HTTP code gets :class:`Permission` through ``identity.dependencies``.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from app.modules.identity.schemas import Role


class Permission(str, enum.Enum):
    """What a role may do in its agency (spec §5.2). The values are the spec's names."""

    workspace_read = "workspace:read"
    campaign_write = "campaign:write"
    campaign_delete = "campaign:delete"
    resume_upload = "resume:upload"
    application_review = "application:review"
    application_delete = "application:delete"
    application_export = "application:export"
    client_read = "client:read"
    client_manage = "client:manage"
    team_read = "team:read"
    team_manage = "team:manage"
    team_manage_privileged = "team:manage_privileged"
    agency_manage = "agency:manage"


P = Permission

_RECRUITER: Final = frozenset(
    {
        P.workspace_read,
        P.campaign_write,
        P.resume_upload,
        P.application_review,
        P.application_delete,
        P.application_export,
        P.client_read,
    }
)

ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = MappingProxyType(
    {
        Role.owner: frozenset(Permission),
        Role.admin: frozenset(Permission) - {P.team_manage_privileged, P.agency_manage},
        # Recruiters may delete and export within their scope; they do not see the team
        # (spec §15, Q4).
        Role.recruiter: _RECRUITER,
        # Read-only; viewers may not export (§15, Q4).
        Role.viewer: frozenset({P.workspace_read, P.client_read}),
    }
)
"""Every role's permissions: exactly the matrix of spec §5.2."""

CLIENT_SCOPED_ROLES: Final = frozenset({Role.recruiter, Role.viewer})
"""Roles whose membership may be limited to some clients (``all_clients`` false); the others
always have every client (``ck_memberships_all_clients``)."""

DENIED: Mapping[Permission, str] = MappingProxyType(
    {
        P.workspace_read: "view this workspace",
        P.campaign_write: "create or edit campaigns",
        P.campaign_delete: "delete campaigns",
        P.resume_upload: "upload resumes",
        P.application_review: "review applications",
        P.application_delete: "delete applications",
        P.application_export: "export applications",
        P.client_read: "view clients",
        P.client_manage: "manage clients",
        P.team_read: "view the team",
        P.team_manage: "manage the team",
        P.team_manage_privileged: "manage owners and admins",
        P.agency_manage: "manage the agency",
    }
)
"""What each permission lets you do, for the 403's detail: "You don't have permission to …"."""


def has_permission(role: Role, permission: Permission) -> bool:
    """Whether ``role`` has ``permission`` in :data:`ROLE_PERMISSIONS`."""
    return permission in ROLE_PERMISSIONS[role]


def permissions_of(role: Role) -> list[Permission]:
    """``role``'s permissions in the order of :class:`Permission` (the order of §5.2)."""
    granted = ROLE_PERMISSIONS[role]
    return [p for p in Permission if p in granted]


def denied_detail(permission: Permission) -> str:
    """The problem detail of a ``403`` for a missing ``permission``."""
    return f"You don't have permission to {DENIED[permission]}"
