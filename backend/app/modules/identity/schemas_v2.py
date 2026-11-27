"""identity resources of the ``/api/v2`` API (EPIC-02 spec §8.2): sign-in, the signed-in user
(``Me``), the active agency, and (E2-07) invitations, password reset and the team's requests.

The team's responses (``Member``, ``Invitation``) name each member's clients, which are hiring's:
they are composed in ``app.api.v2.schemas``.

Public, like ``schemas.py`` (import-linter allows ``app.modules.<name>.schemas_v2``). Class names
are the component names of the v2 OpenAPI document and the generated TypeScript client.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.modules.identity.permissions import Permission
from app.modules.identity.schemas import Role

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class CsrfToken(BaseModel):
    csrf_token: str = Field(
        description="Send it back in the X-CSRF-Token header on POST, PUT, PATCH and DELETE. "
        "The same value is in the (readable) CSRF cookie."
    )


class LoginRequest(BaseModel):
    # Lengths bound the work a request can cause; anything else that is wrong is the same 401.
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)


class AgencyRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class Membership(BaseModel):
    """One agency the user belongs to (active memberships in active agencies only)."""

    agency: AgencyRef
    role: Role


class MeUser(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str


class ClientScope(BaseModel):
    """The clients the user may see in the active agency: all of them, or the listed ones."""

    all_clients: bool
    client_ids: list[uuid.UUID] = Field(description="Empty when all_clients is true.")


class Me(BaseModel):
    """The signed-in user, their agencies and the session's active agency."""

    user: MeUser
    memberships: list[Membership]
    active_agency: AgencyRef | None = Field(
        description="null until the user has chosen an agency (setActiveAgency)."
    )
    role: Role | None = Field(description="The user's role in the active agency.")
    permissions: list[Permission] = Field(
        description="What the role may do in the active agency (the permission matrix), in a "
        "fixed order; empty without an active agency. For showing only what the user can do: "
        "the API checks every request anyway."
    )
    client_scope: ClientScope | None = Field(description="null without an active agency.")
    csrf_token: str = Field(
        description="The CSRF token for this session (also in the CSRF cookie): send it in "
        "X-CSRF-Token on unsafe requests."
    )


class MeUpdate(BaseModel):
    display_name: Name


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(
        max_length=1024,
        description="12 to 128 characters, any characters, not your email address.",
    )


class ActiveAgencyChange(BaseModel):
    agency_id: uuid.UUID


class Agency(BaseModel):
    """The active agency."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str = Field(description="Read-only.")


class AgencyUpdate(BaseModel):
    name: Name


# ── Invitations and password reset (E2-07) ───────────────────
# Tokens are 43 characters (32 random bytes, URL-safe base64); the bound only limits the work
# a request can cause.
Token = Annotated[str, StringConstraints(min_length=1, max_length=200)]
MAX_CLIENT_IDS = 500


class MemberUser(BaseModel):
    """A team member's (or inviter's) account."""

    id: uuid.UUID
    email: str
    display_name: str


class InvitationCreate(BaseModel):
    email: str = Field(max_length=320)
    role: Role = Field(
        description="Admins may invite recruiters and viewers; owners may invite any role."
    )
    all_clients: bool = Field(
        default=True,
        description="Every client of the agency. Always true for owners and admins.",
    )
    client_ids: list[uuid.UUID] = Field(
        default_factory=list,
        max_length=MAX_CLIENT_IDS,
        description="The clients a recruiter or viewer may see when all_clients is false (at "
        "least one); empty when all_clients is true.",
    )


class MemberUpdate(BaseModel):
    """Fields left out (or null) stay as they are."""

    role: Role | None = None
    all_clients: bool | None = None
    client_ids: list[uuid.UUID] | None = Field(default=None, max_length=MAX_CLIENT_IDS)


class InvitationToken(BaseModel):
    token: Token = Field(description="The token from the invitation link (#token=...).")


class InvitationPreview(BaseModel):
    """What an invitation link offers, for the accept page."""

    agency: AgencyRef
    invited_by: str | None = Field(description="The inviter's name (null if unknown).")
    email: str = Field(description="The invited address.")
    role: Role
    account_exists: bool = Field(
        description="An account with this email exists: sign in, then accept. Otherwise accept "
        "with a display name and a password."
    )
    expires_at: datetime


class InvitationAccept(BaseModel):
    token: Token = Field(description="The token from the invitation link (#token=...).")
    display_name: Name | None = Field(
        default=None, description="New accounts only (required for them)."
    )
    password: str | None = Field(
        default=None,
        max_length=1024,
        description="New accounts only (required for them): 12 to 128 characters, any "
        "characters, not your email address.",
    )


class PasswordResetRequest(BaseModel):
    email: str = Field(max_length=320)


class PasswordResetRequested(BaseModel):
    detail: str = Field(description="The same message whether or not the account exists.")


class PasswordResetConfirm(BaseModel):
    token: Token = Field(description="The token from the reset link (#token=...).")
    new_password: str = Field(
        max_length=1024,
        description="12 to 128 characters, any characters, not your email address.",
    )
