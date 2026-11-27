"""``/api/v2/team``: the agency's members and invitations (identity + hiring, E2-07).

Identity owns memberships and invitations, and decides who may do what (spec §5.2: its
``has_team_permission`` / ``can_manage``, and the guard rails: nobody changes their own role or
deactivates themselves, the agency keeps an active owner). Hiring owns the clients a recruiter
or viewer may be limited to: these endpoints check the requested client ids with hiring
(active clients of the agency, 422 otherwise) and name each member's and invitation's clients.

Each route first checks the caller's team permission (``team:read`` for the lists,
``team:manage`` for every change; :func:`team_permission`). When E2-06's permission matrix
merges, that dependency becomes ``require_permission(...)``; the service functions keep their
own checks (owners and admins need ``team:manage_privileged``).

The session is ``tenant_db`` (bound to the caller's agency): hiring's client lookups run under
row-level security, identity's global tables are filtered by the principal's agency in identity.
Invitation emails go out from a Celery task after the commit; an agency may send
``INVITATIONS_MAX_PER_AGENCY_PER_DAY`` (new and resent) a day (429 with ``Retry-After``).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine, Sequence
from typing import Annotated, Any

from fastapi import Depends, status
from fastapi.routing import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v2.schemas import Invitation, InvitationPage, Member, MemberPage
from app.modules.hiring import service as hiring
from app.modules.hiring.schemas_v2 import ClientRef
from app.modules.identity import service as identity
from app.modules.identity.dependencies import (
    client_info,
    current_principal,
    service_errors,
    tenant_db,
)
from app.modules.identity.schemas import Principal, TeamInvitation, TeamMember, TeamUser
from app.modules.identity.schemas_v2 import InvitationCreate, MemberUpdate, MemberUser
from app.platform.pagination import PageParams
from app.platform.problems import FieldError, ProblemError, problem_responses
from app.platform.ratelimit import RateLimitStore, rate_limit_store

router = APIRouter(prefix="/team", tags=["team"])


def team_permission(permission: str) -> Callable[..., Coroutine[Any, Any, None]]:
    """A route dependency: 403 unless the caller's role has the team ``permission``
    (``identity.TEAM_READ`` / ``identity.TEAM_MANAGE``). E2-06: ``require_permission``."""

    async def check(principal: Principal = Depends(current_principal)) -> None:
        with service_errors():
            identity.require_team_permission(principal, permission)

    return check


CAN_READ = [Depends(team_permission(identity.TEAM_READ))]
CAN_MANAGE = [Depends(team_permission(identity.TEAM_MANAGE))]


async def _check_clients(db: AsyncSession, client_ids: Sequence[uuid.UUID] | None) -> None:
    """The requested clients must be active clients of the agency (422 on ``client_ids``)."""
    if not client_ids:
        return
    try:
        await hiring.check_grantable_clients(db, client_ids)
    except hiring.InvalidInputError as exc:
        raise ProblemError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "1 invalid field",
            errors=[FieldError(loc=["body", "client_ids"], msg=exc.detail)],
        ) from None


async def _client_names(
    db: AsyncSession, holders: Sequence[TeamMember | TeamInvitation]
) -> dict[uuid.UUID, ClientRef]:
    return await hiring.client_refs(db, {cid for holder in holders for cid in holder.client_ids})


def _clients(ids: Sequence[uuid.UUID], names: dict[uuid.UUID, ClientRef]) -> list[ClientRef]:
    refs = [names[cid] for cid in ids if cid in names]
    return sorted(refs, key=lambda ref: (ref.name.lower(), str(ref.id)))


def _user(user: TeamUser) -> MemberUser:
    return MemberUser(id=user.id, email=user.email, display_name=user.display_name)


def _member(member: TeamMember, names: dict[uuid.UUID, ClientRef]) -> Member:
    return Member(
        id=member.id,
        user=_user(member.user),
        role=member.role,
        all_clients=member.all_clients,
        clients=_clients(member.client_ids, names),
        status=member.status,
        last_active_at=member.last_active_at,
        joined_at=member.joined_at,
        deactivated_at=member.deactivated_at,
    )


def _invitation(invitation: TeamInvitation, names: dict[uuid.UUID, ClientRef]) -> Invitation:
    return Invitation(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        all_clients=invitation.all_clients,
        clients=_clients(invitation.client_ids, names),
        invited_by=_user(invitation.invited_by) if invitation.invited_by else None,
        status=invitation.status,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
    )


async def _one_member(db: AsyncSession, member: TeamMember) -> Member:
    return _member(member, await _client_names(db, [member]))


async def _one_invitation(db: AsyncSession, invitation: TeamInvitation) -> Invitation:
    return _invitation(invitation, await _client_names(db, [invitation]))


# ── Members ───────────────────────────────────────────────────
@router.get(
    "/members",
    operation_id="listMembers",
    response_model=MemberPage,
    responses=problem_responses(422),
    dependencies=CAN_READ,
)
async def list_members(
    page: Annotated[PageParams, Depends()],
    db: AsyncSession = Depends(tenant_db),
    principal: Principal = Depends(current_principal),
) -> MemberPage:
    """The agency's members, deactivated ones included, by name (owners and admins)."""
    with service_errors():
        members, total = await identity.list_members(db, principal, page.offset, page.limit)
    names = await _client_names(db, members)
    return MemberPage(
        items=[_member(m, names) for m in members],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.patch(
    "/members/{membership_id}",
    operation_id="updateMember",
    response_model=Member,
    responses=problem_responses(404, 409, 422),
    dependencies=CAN_MANAGE,
)
async def update_member(
    membership_id: uuid.UUID,
    payload: MemberUpdate,
    db: AsyncSession = Depends(tenant_db),
    principal: Principal = Depends(current_principal),
    client: identity.ClientInfo = Depends(client_info),
) -> Member:
    """Change a member's role or client access. Admins manage recruiters and viewers; only
    owners manage owners and admins (as the old or the new role). Nobody changes their own role
    (403), and the last active owner cannot be demoted (409). Owners and admins always have
    every client; a recruiter or viewer limited to some clients needs at least one."""
    await _check_clients(db, payload.client_ids)
    with service_errors():
        member = await identity.update_member(
            db,
            principal,
            membership_id,
            role=payload.role,
            all_clients=payload.all_clients,
            client_ids=payload.client_ids,
            client=client,
        )
    return await _one_member(db, member)


@router.post(
    "/members/{membership_id}/deactivate",
    operation_id="deactivateMember",
    response_model=Member,
    responses=problem_responses(404, 409, 422),
    dependencies=CAN_MANAGE,
)
async def deactivate_member(
    membership_id: uuid.UUID,
    db: AsyncSession = Depends(tenant_db),
    principal: Principal = Depends(current_principal),
    client: identity.ClientInfo = Depends(client_info),
) -> Member:
    """End a member's access to the agency, from their next request (their account and other
    agencies are unaffected). Not yourself (403), not the last active owner (409)."""
    with service_errors():
        member = await identity.set_member_status(
            db, principal, membership_id, active=False, client=client
        )
    return await _one_member(db, member)


@router.post(
    "/members/{membership_id}/reactivate",
    operation_id="reactivateMember",
    response_model=Member,
    responses=problem_responses(404, 422),
    dependencies=CAN_MANAGE,
)
async def reactivate_member(
    membership_id: uuid.UUID,
    db: AsyncSession = Depends(tenant_db),
    principal: Principal = Depends(current_principal),
    client: identity.ClientInfo = Depends(client_info),
) -> Member:
    """Give a deactivated member their access back, with the role and clients they had."""
    with service_errors():
        member = await identity.set_member_status(
            db, principal, membership_id, active=True, client=client
        )
    return await _one_member(db, member)


# ── Invitations ───────────────────────────────────────────────
@router.get(
    "/invitations",
    operation_id="listInvitations",
    response_model=InvitationPage,
    responses=problem_responses(422),
    dependencies=CAN_READ,
)
async def list_invitations(
    page: Annotated[PageParams, Depends()],
    db: AsyncSession = Depends(tenant_db),
    principal: Principal = Depends(current_principal),
) -> InvitationPage:
    """Open invitations (pending and expired), newest first."""
    with service_errors():
        invitations, total = await identity.list_invitations(db, principal, page.offset, page.limit)
    names = await _client_names(db, invitations)
    return InvitationPage(
        items=[_invitation(i, names) for i in invitations],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post(
    "/invitations",
    operation_id="createInvitation",
    status_code=status.HTTP_201_CREATED,
    response_model=Invitation,
    responses=problem_responses(409, 422, 429),
    dependencies=CAN_MANAGE,
)
async def create_invitation(
    payload: InvitationCreate,
    db: AsyncSession = Depends(tenant_db),
    principal: Principal = Depends(current_principal),
    store: RateLimitStore = Depends(rate_limit_store),
    client: identity.ClientInfo = Depends(client_info),
) -> Invitation:
    """Invite someone by email: they get a link (`/accept-invite#token=...`) that works for 7
    days. Admins invite recruiters and viewers; owners invite any role. An open invitation for
    the same address is replaced (its link stops working); an active member's address is a 409.
    An agency sends at most 50 invitations a day (429)."""
    await _check_clients(db, payload.client_ids)
    with service_errors():
        invitation = await identity.create_invitation(
            db,
            store,
            principal,
            email=payload.email,
            role=payload.role,
            all_clients=payload.all_clients,
            client_ids=payload.client_ids,
            client=client,
        )
    return await _one_invitation(db, invitation)


@router.post(
    "/invitations/{invitation_id}/resend",
    operation_id="resendInvitation",
    response_model=Invitation,
    responses=problem_responses(404, 409, 422, 429),
    dependencies=CAN_MANAGE,
)
async def resend_invitation(
    invitation_id: uuid.UUID,
    db: AsyncSession = Depends(tenant_db),
    principal: Principal = Depends(current_principal),
    store: RateLimitStore = Depends(rate_limit_store),
    client: identity.ClientInfo = Depends(client_info),
) -> Invitation:
    """Send the invitation again with a new link and a new 7-day expiry (the old link stops
    working); this also renews an expired one."""
    with service_errors():
        invitation = await identity.resend_invitation(
            db, store, principal, invitation_id, client=client
        )
    return await _one_invitation(db, invitation)


@router.delete(
    "/invitations/{invitation_id}",
    operation_id="revokeInvitation",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=problem_responses(404, 422),
    dependencies=CAN_MANAGE,
)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    db: AsyncSession = Depends(tenant_db),
    principal: Principal = Depends(current_principal),
    client: identity.ClientInfo = Depends(client_info),
) -> None:
    """Revoke an open invitation: its link stops working."""
    with service_errors():
        await identity.revoke_invitation(db, principal, invitation_id, client=client)
