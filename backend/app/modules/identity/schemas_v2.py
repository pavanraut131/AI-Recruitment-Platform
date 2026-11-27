"""identity resources of the ``/api/v2`` API (EPIC-02 spec §8.2): sign-in, the signed-in user
(``Me``) and the active agency.

Public, like ``schemas.py`` (import-linter allows ``app.modules.<name>.schemas_v2``). Class names
are the component names of the v2 OpenAPI document and the generated TypeScript client.
"""

import uuid
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
