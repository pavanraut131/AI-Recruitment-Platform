"""hiring service: the public interface of the hiring module.

Clients, campaigns and applications: the ``/api/v2`` use cases (EPIC-01 spec §6, EPIC-02 §8),
which return the resources of ``schemas_v2``, job description parsing, and what the other modules
need (the upload pipeline's campaign lookups and application upsert, the verification's reads).
Other modules (``ingestion``) and the composition layer (``app.api.v2``) may import this module
and ``schemas_v2``, never ``models`` or ``repository`` (import-linter enforces it).

Business rules live here: the application filters (the ones on the extraction snapshot are
evaluated by ingestion), the field sources of an application and its CSV row, the stats
aggregation and the clients' rules. Job descriptions are parsed by the ``ai`` module. Errors are
raised as :class:`HiringError` subclasses carrying the user-facing ``detail``; the routers map
them to status codes.

An application's fields (EPIC-00 spec §7)
-----------------------------------------
An application is one row per (campaign, Person). Its resource is built from two sources, field
by field:

- the **application** (hiring): ``id``, ``campaign_id``, ``resume_id``, the scoring and
  insights, ``category``, ``notes``, ``pipeline_stage``, ``created_at``, ``updated_at``;
- the **extraction snapshot of the application's current resume** (``resumes.extracted``,
  ingestion): name, contact details, URLs, skills and years of experience. These are
  per-resume facts. The Person's profile is deliberately not used: it follows the person's newest
  resume across *all* campaigns, so it can differ from what this application was scored on.

Deletes remove the application and every resume of its person in its campaign, and the resumes'
stored PDFs. Until E2-12 the ``/api/v1`` candidate endpoints served the same applications in the
pre-E0 "candidate" shape and also resolved the ids of legacy candidates merged by the E0
backfill (``legacy_candidate_map``); both were removed with v1.

Cross-module boundaries (E0-10)
-------------------------------
Hiring never reads or writes another module's tables. Where a use case needs another module,
the data crosses this interface instead:

1. **Resume tallies** (the campaigns' counts, Insights, the stats): the use cases take them as
   an argument or through a :class:`ResumeTallies` port (``ingestion.resume_tallies(db)``);
   ``app.api.v2`` composes the endpoint.
2. **Resume extractions** (E0-19): the application resources, the list's profile filters and
   name sort, the export, and the Insights' top skills read the resumes' extraction snapshots
   through a :class:`ResumeProfiles` port (ingestion's ``resume_profiles(db)``).
3. **Deleting applications** (``DELETE /applications/{id}``, the bulk ``delete``) also deletes
   resumes. :func:`delete_application_v2` and :func:`bulk_applications` delete the applications
   (flush, don't commit) and return what ingestion needs to delete their resumes;
   ``app.api.v2`` has ingestion delete them and commits both in one transaction, then has
   ingestion delete the stored PDFs.
4. **Deleting a campaign** (E0-20): :func:`delete_campaign` deletes the campaign and, by the
   same-module foreign key, its applications (flush, don't commit); ``app.api.v2`` has ingestion
   delete the campaign's resumes in the same transaction and their PDFs after the commit. No
   foreign key links ``resumes`` to ``campaigns`` (ADR 0003).
5. **The upload pipeline** (``app.modules.ingestion``) checks campaigns through
   :func:`campaign_exists` / :func:`get_campaign_requirements` and writes applications through
   :func:`upsert_application`. Its scoring input is the ``ai`` module's ``ScoringResult``
   (``app.modules.ai.schemas``, public): the pipeline passes on what the model returned, and
   hiring keeps no second copy of that shape to keep in sync. The verification command
   (``app.modules.ingestion.verify_people_applications``) reads applications through
   :func:`application_refs`.

Tenancy (EPIC-02)
-----------------
The session is bound to an agency (``app.platform.tenancy``, E2-03): the repository filters
every query to it, and every insert takes its ``agency_id`` from it (:func:`_new_campaign`,
:func:`upsert_application`).

**Clients (E2-06, spec §3.1, §8).** The agency's customers: listed, created, renamed and archived
here (never deleted: campaigns refer to them). Every campaign is run for one client: the one its
create request names, which must be an active client in the caller's scope, else the caller's
default client (:func:`_campaign_client`); an update may move it to another such client.

**Client scoping (E2-06, spec §5.3).** The use cases that list or fetch campaign data take an
``AccessScope``: :func:`check_scope` checks its agency, and the repository keeps every query to
its clients. Lists (campaigns, a campaign's applications, Home, stats, Insights, clients) show
only what the scope covers; a single campaign, application or client outside it is
:class:`NotFoundError` (a 404, as for another agency's), so nothing confirms that it exists. The
talent pool stays agency-wide (``talent``), but a Person's applications to campaigns outside the
scope are left out (:func:`person_applications`, :func:`people_activity`). A request's
``client_id`` filter narrows the scope (``AccessScope.restricted_to``), never widens it.

Rows written before E2-01 get their agency from the tenancy backfill
(``app.modules.ingestion.backfill_tenancy``, E2-02) through :func:`fill_tenancy_batch`; it and
the verification's reads (:func:`application_refs`, :func:`campaign_tenancy`,
:func:`client_agencies`, :func:`agency_census`) read **every agency** on purpose (operator
commands: unbound sessions as the owner role, ``app.platform.maintenance``).
"""

import csv
import dataclasses
import io
import uuid
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import structlog
from sqlalchemy import ColumnElement, any_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.ai import service as ai_service
from app.modules.ai.schemas import ScoringResult
from app.modules.hiring import repository
from app.modules.hiring import schemas_v2 as v2
from app.modules.hiring.models import Application, Campaign, Client, ClientStatus
from app.platform.sql import LIKE_ESCAPE, escape_like, uuid_array
from app.platform.tenancy import AccessScope, agency_id_of, require_scope

logger = structlog.get_logger()

PROCESSING_STATUSES = ("pending", "extracting", "processing")
TERMINAL_FAILED_STATUSES = ("failed", "quota_exceeded")


# ── Errors ────────────────────────────────────────────────────
class HiringError(Exception):
    """A hiring operation failed; ``detail`` is the message returned to the client."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFoundError(HiringError):
    """The campaign, application or client does not exist, or is outside the caller's scope (the
    same answer, spec §5.3)."""


class InvalidInputError(HiringError):
    """The request is invalid in a way its schema cannot check (e.g. clients that cannot be
    granted)."""


class InvalidFieldError(InvalidInputError):
    """A request body field the service rejects (``field``): a 422 naming it."""

    def __init__(self, field: str, detail: str) -> None:
        super().__init__(detail)
        self.field = field


class ConflictError(HiringError):
    """The change conflicts with existing data (a client name taken in the agency)."""


class JDParseError(HiringError):
    """The job description could not be parsed (AI provider not configured, or the call or its
    answer failed)."""


# ── Tenancy ───────────────────────────────────────────────────
def check_scope(db: AsyncSession, scope: AccessScope) -> None:
    """``scope`` must be of the session's agency (``TenantMismatch`` otherwise). The use case
    then keeps to the scope's clients (spec §5.3)."""
    require_scope(db, scope)


async def campaign_ids_in_scope(db: AsyncSession, scope: AccessScope) -> list[uuid.UUID] | None:
    """The campaigns ``scope`` covers, for another module's lists of campaign-owned rows
    (ingestion's resumes): ``None`` when it covers every client (no filter needed, no query),
    else their ids (one query)."""
    check_scope(db, scope)
    if scope.all_clients:
        return None
    return await repository.campaign_ids_in(db, scope)


# ── Clients ───────────────────────────────────────────────────
def client_resource(client: Client) -> v2.Client:
    return v2.Client(
        id=client.id,
        name=client.name,
        status=v2.ClientStatus(client.status),
        created_at=client.created_at,
        updated_at=client.updated_at,
    )


@dataclass(frozen=True)
class ClientQuery:
    """The client list filters: any of ``statuses``; ``q`` contained in the name."""

    statuses: Sequence[str] = ()
    q: str | None = None


async def list_clients(
    db: AsyncSession, scope: AccessScope, query: ClientQuery, offset: int, limit: int
) -> tuple[list[v2.Client], int]:
    """A page of the clients in ``scope``, by name, and how many match: two queries."""
    check_scope(db, scope)
    clients, total = await repository.find_clients(
        db, scope, query.statuses, query.q, offset=offset, limit=limit
    )
    return [client_resource(c) for c in clients], total


async def _require_client(db: AsyncSession, scope: AccessScope, client_id: uuid.UUID) -> Client:
    client = await repository.get_client(db, scope, client_id)
    if client is None:
        raise NotFoundError("Client not found")
    return client


async def get_client(db: AsyncSession, scope: AccessScope, client_id: uuid.UUID) -> v2.Client:
    """The client (404 outside the agency or the scope)."""
    check_scope(db, scope)
    return client_resource(await _require_client(db, scope, client_id))


DUPLICATE_CLIENT = "A client with this name already exists"


async def create_client(
    db: AsyncSession, scope: AccessScope, payload: v2.ClientCreate
) -> v2.Client:
    """A new active client of the agency. A name the agency already uses (ignoring case, any
    status) is a :class:`ConflictError`. Flushes, does not commit: the caller records the
    audit event in the same transaction and commits."""
    check_scope(db, scope)
    if await repository.client_named(db, payload.name) is not None:
        raise ConflictError(DUPLICATE_CLIENT)
    client = Client(
        id=uuid.uuid4(),
        agency_id=agency_id_of(db),
        name=payload.name,
        status=ClientStatus.active.value,
    )
    try:
        await repository.flush_client(db, client)
    except repository.DuplicateClientName:
        raise ConflictError(DUPLICATE_CLIENT) from None
    logger.info("client_created", client_id=str(client.id))
    return client_resource(client)


@dataclass(frozen=True)
class ClientChange:
    """An updated client and what changed: ``field -> (old, new)`` (empty: nothing did)."""

    client: v2.Client
    changes: Mapping[str, tuple[str, str]]


async def update_client(
    db: AsyncSession, scope: AccessScope, client_id: uuid.UUID, payload: v2.ClientUpdate
) -> ClientChange:
    """Rename, archive or restore the client (only the fields sent; 404 outside the agency or
    the scope; a name taken by another client is a :class:`ConflictError`). Archiving keeps its
    campaigns as they are. Flushes, does not commit: the caller records the audit event and
    commits."""
    check_scope(db, scope)
    client = await _require_client(db, scope, client_id)
    sent = payload.model_dump(exclude_unset=True, mode="json")
    changes = {
        name: (getattr(client, name), value)
        for name, value in sent.items()
        if getattr(client, name) != value
    }
    if "name" in changes and await repository.client_named(
        db, changes["name"][1], other_than=client.id
    ):
        raise ConflictError(DUPLICATE_CLIENT)
    for name, (_, value) in changes.items():
        setattr(client, name, value)
    if changes:
        try:
            await repository.flush_client(db, client)
        except repository.DuplicateClientName:
            raise ConflictError(DUPLICATE_CLIENT) from None
        logger.info("client_updated", client_id=str(client.id), fields=sorted(changes))
    return ClientChange(client_resource(client), changes)


GENERAL_CLIENT = "General"


async def ensure_first_client(db: AsyncSession) -> bool:
    """Give the session's agency a client named "General" if it has no client at all (spec §15
    Q7's name for migrated data; the E2-06 migration did the same for every agency then).
    Returns whether it created one. Flushes; the caller commits. For the development seed."""
    if await repository.count_clients(db):
        return False
    await repository.flush_client(
        db,
        Client(id=uuid.uuid4(), agency_id=agency_id_of(db), name=GENERAL_CLIENT, status="active"),
    )
    return True


# ── Clients of team members and invitations (E2-07) ──────────
async def client_refs(
    db: AsyncSession, scope: AccessScope, client_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, v2.ClientRef]:
    """The clients in ``scope`` with these ids, archived ones included (a member keeps access to
    an archived client's campaigns), by id: the names of a member's or an invitation's clients.
    Ids of no such client are left out."""
    check_scope(db, scope)
    rows = await repository.clients_by_ids(db, scope, sorted(set(client_ids)))
    return {client.id: v2.ClientRef.model_validate(client) for client in rows}


async def check_grantable_clients(
    db: AsyncSession, scope: AccessScope, client_ids: Collection[uuid.UUID]
) -> None:
    """Every id must be an **active** client in ``scope`` (the granting user's own: nobody
    grants access to a client they cannot see), else :class:`InvalidInputError` (the same
    message whether a client is archived, outside the scope, another agency's or unknown)."""
    check_scope(db, scope)
    wanted = set(client_ids)
    rows = await repository.clients_by_ids(db, scope, sorted(wanted))
    active = {client.id for client in rows if client.status == ClientStatus.active.value}
    if wanted - active:
        raise InvalidInputError("Choose clients of this agency that are not archived")


# ── Campaigns ─────────────────────────────────────────────────
def _new_campaign(
    db: AsyncSession,
    fields: Mapping[str, Any],
    client_id: uuid.UUID,
    created_by: uuid.UUID | None = None,
) -> Campaign:
    """A campaign to insert, in the session's agency, run for ``client_id``."""
    return Campaign(
        **fields, agency_id=agency_id_of(db), client_id=client_id, created_by_user_id=created_by
    )


async def _require_campaign(
    db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID
) -> Campaign:
    campaign = await repository.get_campaign(db, scope, campaign_id)
    if not campaign:
        raise NotFoundError("Campaign not found")
    return campaign


async def delete_campaign(db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID) -> None:
    """Delete the campaign and its applications (``applications.campaign_id ON DELETE
    CASCADE``).

    Flushes but does not commit: the caller (``app.api.v2``) has ingestion delete the campaign's
    resumes in the same transaction and commits. Raises :class:`NotFoundError` for an unknown
    campaign or one outside the scope.
    """
    check_scope(db, scope)
    campaign = await _require_campaign(db, scope, campaign_id)
    await repository.delete_campaign(db, campaign)
    logger.info("campaign_deleted", id=str(campaign_id))


# ── The filters on the extraction snapshot ────────────────────
@dataclass(frozen=True)
class ProfileFilters:
    """The list filters that read a resume's extraction snapshot (``resumes.extracted``), which
    ingestion evaluates (B2): ``search`` is contained in the name or the email, ``skill`` in any
    one skill, both case-insensitively and with LIKE wildcards taken literally;
    ``min_experience`` is inclusive and never matches an unknown experience."""

    search: str | None = None
    skill: str | None = None
    min_experience: float | None = None

    @property
    def active(self) -> bool:
        return bool(self.search) or bool(self.skill) or self.min_experience is not None


# ── Resume extractions (ingestion's data, E0-19) ──────────────
@dataclass(frozen=True)
class ResumeOwner:
    """The campaign a resume was uploaded to and the Person it resolved to (``None`` until it
    is resolved)."""

    campaign_id: uuid.UUID
    person_id: uuid.UUID | None


class ResumeProfiles(Protocol):
    """What the application use cases need from ingestion's ``resumes`` (``app.api.v2`` passes
    ``ingestion.service.resume_profiles(db)``). Unknown resume ids are left out of every answer."""

    async def extractions(
        self, resume_ids: Sequence[uuid.UUID]
    ) -> Mapping[uuid.UUID, Mapping[str, Any] | None]:
        """``resumes.extracted`` of each resume (``None``: not extracted)."""
        ...

    async def matching(
        self, resume_ids: Sequence[uuid.UUID], filters: ProfileFilters
    ) -> Collection[uuid.UUID]:
        """The resumes among ``resume_ids`` whose extraction matches ``filters``."""
        ...

    async def skill_lists(self, resume_ids: Sequence[uuid.UUID]) -> Mapping[uuid.UUID, Any]:
        """The extracted ``skills`` of each resume."""
        ...

    async def names(self, resume_ids: Sequence[uuid.UUID]) -> Mapping[uuid.UUID, str | None]:
        """The extracted ``name`` of each resume (the v2 list's ``sort=name``)."""
        ...


# ── An application's fields from its resume's extraction (EPIC-00 spec §7) ──
# From the extraction snapshot of the application's current resume (the pre-E0-19 pipeline
# copied the same ``ExtractionResult`` onto the legacy candidate). A missing snapshot reads as no
# values and empty lists.
_SNAPSHOT_VALUES = ("name", "email", "phone", "github_url", "linkedin_url")
_SNAPSHOT_LISTS = ("skills", "education", "work_experience", "certifications", "projects")


def _profile(extracted: Mapping[str, Any] | None) -> dict[str, Any]:
    snapshot = extracted or {}
    profile: dict[str, Any] = {name: snapshot.get(name) for name in _SNAPSHOT_VALUES}
    for name in _SNAPSHOT_LISTS:
        value = snapshot.get(name)
        profile[name] = value if value is not None else []
    years = snapshot.get("years_of_experience")
    # A float in the resources; JSON may hold it as an integer.
    profile["years_of_experience"] = float(years) if years is not None else None
    return profile


async def _snapshots(
    profiles: ResumeProfiles, applications: Sequence[Application]
) -> Mapping[uuid.UUID, Mapping[str, Any] | None]:
    """The extraction of every application's current resume, in one lookup (no N+1)."""
    if not applications:
        return {}
    return await profiles.extractions([a.resume_id for a in applications])


# ── The CSV export ────────────────────────────────────────────
# The columns of the pre-E1 export, which v2's keeps (EPIC-01 spec §6).
CSV_FIELDS = [
    "Name",
    "Email",
    "Phone",
    "Score",
    "Category",
    "Pipeline Stage",
    "Years Experience",
    "Skills",
    "Missing Skills",
    "Strengths",
    "Recommendation",
    "LinkedIn URL",
    "GitHub URL",
]


def _join(values: Sequence[Any] | None) -> str:
    return " | ".join(str(v) for v in (values or []))


def render_applications_csv(
    rows: Iterable[tuple[Application, Mapping[str, Any] | None]],
) -> str:
    """Each ``(application, its current resume's extraction)`` as one CSV line."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for application, extracted in rows:
        profile = _profile(extracted)
        years = profile["years_of_experience"]
        writer.writerow(
            {
                "Name": profile["name"] or "",
                "Email": profile["email"] or "",
                "Phone": profile["phone"] or "",
                "Score": f"{application.score:.0f}" if application.score is not None else "",
                "Category": application.category or "",
                "Pipeline Stage": application.pipeline_stage or "",
                "Years Experience": f"{years:.1f}" if years is not None else "",
                "Skills": _join(profile["skills"]),
                "Missing Skills": _join(application.missing_skills),
                "Strengths": _join(application.strengths),
                "Recommendation": application.recommendation or "",
                "LinkedIn URL": profile["linkedin_url"] or "",
                "GitHub URL": profile["github_url"] or "",
            }
        )
    return output.getvalue()


@dataclass(frozen=True)
class DeletedApplication:
    """A deleted application: ingestion deletes its resume and every other resume of its Person
    in its campaign (``ingestion.service.delete_application_resumes``)."""

    id: uuid.UUID
    campaign_id: uuid.UUID
    person_id: uuid.UUID
    resume_id: uuid.UUID


async def _delete_applications(
    db: AsyncSession, applications: Sequence[Application]
) -> list[DeletedApplication]:
    deleted = [
        DeletedApplication(
            id=a.id, campaign_id=a.campaign_id, person_id=a.person_id, resume_id=a.resume_id
        )
        for a in applications
    ]
    for application in applications:
        await repository.delete_application(db, application)
    await repository.flush(db)
    return deleted


# ══ /api/v2 resources (E1-02, EPIC-01 spec §6) ════════════════
# The v2 use cases return the resources of ``schemas_v2``. They fixed the bugs the pre-E1 API
# recorded: unknown campaigns are a 404 (B7), the export applies every list filter (B8),
# ``avg_score`` is the mean over the scored applications (B9), and invalid values are rejected by
# the request schema (422) before they get here (B5, B10).
STRONG_MATCH = v2.ApplicationCategory.strong_match.value
REJECTED_STAGE = v2.PipelineStage.rejected_manual.value
TOP_SKILLS = 10


@dataclass(frozen=True)
class ResumeTally:
    """What ingestion reports about the resumes of a campaign (or of all campaigns): counts by
    status, how many were uploaded in the last 24 hours, and the latest change to any of them."""

    by_status: Mapping[str, int] = dataclasses.field(default_factory=dict)
    uploaded_last_24h: int = 0
    last_updated_at: datetime | None = None


class ResumeTallies(Protocol):
    """The resume tallies of several campaigns (``app.api.v2`` passes
    ``ingestion.service.resume_tallies(db)``)."""

    async def by_campaign(
        self, campaign_ids: Sequence[uuid.UUID]
    ) -> Mapping[uuid.UUID, ResumeTally]:
        """One tally per campaign that has resumes (one query); others are left out."""
        ...


def _status(value: str) -> v2.CampaignStatus:
    return v2.CampaignStatus(value)


def _category(value: str | None) -> v2.ApplicationCategory | None:
    return v2.ApplicationCategory(value) if value is not None else None


def _stage(value: str | None) -> v2.PipelineStage | None:
    return v2.PipelineStage(value) if value is not None else None


def _campaign_ref(campaign: Campaign) -> v2.CampaignRef:
    return v2.CampaignRef(id=campaign.id, title=campaign.title, status=_status(campaign.status))


def resume_counts(tally: ResumeTally) -> v2.ResumeCounts:
    by_status = tally.by_status
    return v2.ResumeCounts(
        total=sum(by_status.values()),
        pending=by_status.get("pending", 0),
        extracting=by_status.get("extracting", 0),
        processing=by_status.get("processing", 0),
        done=by_status.get("done", 0),
        failed=by_status.get("failed", 0),
        quota_exceeded=by_status.get("quota_exceeded", 0),
    )


def campaign_resource(
    campaign: Campaign,
    applications: tuple[int, int, datetime | None] | None,
    resumes: ResumeTally,
) -> v2.Campaign:
    """The v2 ``Campaign``, given its ``(applications, strong matches, latest application
    change)`` and its resume tally. The last activity is the latest change to the campaign, any
    of its applications or any of its resumes."""
    count, strong, latest_application = applications or (0, 0, None)
    by_status = resumes.by_status
    changes = [campaign.updated_at, latest_application, resumes.last_updated_at]
    return v2.Campaign(
        id=campaign.id,
        title=campaign.title,
        role=campaign.role,
        description=campaign.description,
        required_skills=campaign.required_skills or [],
        status=_status(campaign.status),
        client=v2.ClientRef(id=campaign.client.id, name=campaign.client.name),
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
        last_activity_at=max(t for t in changes if t is not None),
        counts=v2.CampaignCounts(
            resumes=sum(by_status.values()),
            in_progress=sum(by_status.get(s, 0) for s in PROCESSING_STATUSES),
            failed=sum(by_status.get(s, 0) for s in TERMINAL_FAILED_STATUSES),
            new_resumes_24h=resumes.uploaded_last_24h,
            applications=count,
            strong_matches=strong,
        ),
    )


async def _campaign_resources(
    db: AsyncSession, campaigns: Sequence[Campaign], tallies: ResumeTallies
) -> list[v2.Campaign]:
    """Every campaign's resource, with two grouped queries whatever their number (no N+1)."""
    ids = [c.id for c in campaigns]
    if not ids:
        return []
    applications = await repository.application_tallies(db, ids, STRONG_MATCH)
    resumes = await tallies.by_campaign(ids)
    return [
        campaign_resource(c, applications.get(c.id), resumes.get(c.id, ResumeTally()))
        for c in campaigns
    ]


# ── v2: campaigns ─────────────────────────────────────────────
@dataclass(frozen=True)
class CampaignQuery:
    """The campaign list filters: any of ``statuses``; ``q`` contained in the title or the role
    (case-insensitive, wildcards literal)."""

    statuses: Sequence[str] = ()
    q: str | None = None


def _campaign_clauses(query: CampaignQuery) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = []
    if query.statuses:
        clauses.append(Campaign.status.in_(list(query.statuses)))
    if query.q:
        pattern = f"%{escape_like(query.q)}%"
        clauses.append(
            or_(
                Campaign.title.ilike(pattern, escape=LIKE_ESCAPE),
                Campaign.role.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    return clauses


async def list_campaigns_page(
    db: AsyncSession,
    scope: AccessScope,
    query: CampaignQuery,
    offset: int,
    limit: int,
    tallies: ResumeTallies,
) -> tuple[list[v2.Campaign], int]:
    """A page of the campaigns in ``scope``, newest first, and how many match: four queries."""
    check_scope(db, scope)
    campaigns, total = await repository.find_campaigns(
        db, scope, _campaign_clauses(query), offset=offset, limit=limit
    )
    return await _campaign_resources(db, campaigns, tallies), total


NO_DEFAULT_CLIENT = "Choose a client: there is no active client you can use"


async def _campaign_client(
    db: AsyncSession, scope: AccessScope, client_id: uuid.UUID | None
) -> Client:
    """The client a campaign may be run for: ``client_id``, which must be an active client in
    ``scope``; ``None`` is the caller's default client, the oldest active client in ``scope``.
    Anything else is an :class:`InvalidFieldError` on ``client_id`` (a client outside the scope
    reads like an unknown one)."""
    if client_id is None:
        default = await repository.default_client(db, scope)
        if default is None:
            raise InvalidFieldError("client_id", NO_DEFAULT_CLIENT)
        return default
    client = await repository.get_client(db, scope, client_id)
    if client is None:
        raise InvalidFieldError("client_id", "Unknown client")
    if client.status != ClientStatus.active.value:
        raise InvalidFieldError("client_id", "This client is archived")
    return client


async def create_campaign_v2(
    db: AsyncSession,
    scope: AccessScope,
    payload: v2.CampaignCreate,
    created_by: uuid.UUID | None = None,
) -> v2.Campaign:
    """Create a campaign for the client the payload names (or the caller's default client,
    :func:`_campaign_client`), recording who created it. A new campaign has no resumes or
    applications yet, so its counts are zero without asking ingestion."""
    check_scope(db, scope)
    client = await _campaign_client(db, scope, payload.client_id)
    fields = payload.model_dump(mode="json", exclude={"client_id"})
    campaign = await repository.add_campaign(db, _new_campaign(db, fields, client.id, created_by))
    logger.info("campaign_created", id=str(campaign.id), title=campaign.title)
    return campaign_resource(campaign, None, ResumeTally())


async def get_campaign_v2(
    db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID, tallies: ResumeTallies
) -> v2.Campaign:
    check_scope(db, scope)
    campaign = await _require_campaign(db, scope, campaign_id)
    (resource,) = await _campaign_resources(db, [campaign], tallies)
    return resource


async def update_campaign_v2(
    db: AsyncSession,
    scope: AccessScope,
    campaign_id: uuid.UUID,
    payload: v2.CampaignUpdate,
    tallies: ResumeTallies,
) -> v2.Campaign:
    """Apply the fields the client sent (unset fields are left alone). A new ``client_id`` must
    be an active client in ``scope``, like the campaign itself (spec §8.3)."""
    check_scope(db, scope)
    campaign = await _require_campaign(db, scope, campaign_id)
    changes = payload.model_dump(exclude_unset=True, mode="json")
    if "client_id" in changes:
        moved_to = uuid.UUID(changes["client_id"])
        if moved_to == campaign.client_id:
            del changes["client_id"]
        else:
            changes["client_id"] = (await _campaign_client(db, scope, moved_to)).id
    for name, value in changes.items():
        setattr(campaign, name, value)
    await repository.commit_and_refresh(db, campaign)
    logger.info("campaign_updated", id=str(campaign_id), fields=sorted(changes))
    (resource,) = await _campaign_resources(db, [campaign], tallies)
    return resource


async def campaign_insights(
    db: AsyncSession,
    scope: AccessScope,
    campaign_id: uuid.UUID,
    resumes: ResumeTally,
    profiles: ResumeProfiles,
) -> v2.CampaignStats:
    """The Insights numbers of one campaign (404 for an unknown one): resume counts by status,
    application counts by category and stage, the mean score, a score histogram and the top
    skills (from each application's current resume, oldest application first so equal counts
    keep a stable order), flagged when the campaign requires them. Six queries."""
    check_scope(db, scope)
    campaign = await _require_campaign(db, scope, campaign_id)
    category_rows = await repository.category_score_rows(db, scope, campaign_id)
    stages = await repository.stage_counts(db, scope, campaign_id)
    buckets = await repository.score_buckets(db, campaign_id)
    resume_ids = await repository.campaign_resume_ids(db, campaign_id)
    skills = await profiles.skill_lists(resume_ids) if resume_ids else {}
    return v2.CampaignStats(
        resumes=resume_counts(resumes),
        applications=application_counts(category_rows, stages),
        avg_score=mean_score(category_rows),
        score_histogram=[
            v2.ScoreBucket(min=i * 10, max=(i + 1) * 10, count=buckets.get(i, 0)) for i in range(10)
        ],
        top_skills=top_skills(
            (skills.get(rid) for rid in resume_ids), campaign.required_skills or []
        ),
    )


def application_counts(
    category_rows: Iterable[tuple[str | None, int, int, float | None]],
    stages: Mapping[str | None, int],
) -> v2.ApplicationCounts:
    """Application counts by category and by stage (unknown values count in the total only)."""
    rows = list(category_rows)
    by_category = {category: count for category, count, _, _ in rows}
    return v2.ApplicationCounts(
        total=sum(count for _, count, _, _ in rows),
        by_category=v2.CategoryCounts(
            strong_match=by_category.get("strong_match", 0),
            moderate_match=by_category.get("moderate_match", 0),
            weak_match=by_category.get("weak_match", 0),
            rejected=by_category.get("rejected", 0),
        ),
        by_stage=v2.StageCounts(
            unreviewed=stages.get(None, 0),
            screened=stages.get("screened", 0),
            phone_call=stages.get("phone_call", 0),
            technical=stages.get("technical", 0),
            offer=stages.get("offer", 0),
            hired=stages.get("hired", 0),
            rejected_manual=stages.get("rejected_manual", 0),
        ),
    )


def mean_score(category_rows: Iterable[tuple[str | None, int, int, float | None]]) -> float | None:
    """The mean over every scored application, to 2 decimals (the pre-E1 ``avg_score`` was a mean
    of per-category means that also dropped a 0 mean: B9)."""
    scored = 0
    total = 0.0
    for _, _, n_scored, score_sum in category_rows:
        scored += n_scored
        total += score_sum or 0.0
    return round(total / scored, 2) if scored else None


def top_skills(
    skill_lists: Iterable[list[str] | None], required: Sequence[str]
) -> list[v2.SkillCount]:
    """The :data:`TOP_SKILLS` most frequent skills (ties keep first-seen order), each flagged when
    it is one of ``required`` (compared case-insensitively)."""
    counts: dict[str, int] = {}
    for skills in skill_lists:
        for skill in skills or []:
            counts[skill] = counts.get(skill, 0) + 1
    wanted = {skill.casefold() for skill in required}
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:TOP_SKILLS]
    return [
        v2.SkillCount(skill=skill, count=count, required=skill.casefold() in wanted)
        for skill, count in ranked
    ]


async def workspace_stats(
    db: AsyncSession, scope: AccessScope, resumes: ResumeTally
) -> v2.WorkspaceStats:
    """Counts across every campaign in ``scope``, given their resumes' tally (from ingestion):
    three queries."""
    check_scope(db, scope)
    campaign_counts = await repository.campaign_status_counts(db, scope)
    category_rows = await repository.category_score_rows(db, scope)
    stages = await repository.stage_counts(db, scope)
    return v2.WorkspaceStats(
        campaigns=v2.CampaignStatusCounts(
            total=sum(campaign_counts.values()),
            active=campaign_counts.get("active", 0),
            closed=campaign_counts.get("closed", 0),
            archived=campaign_counts.get("archived", 0),
        ),
        resumes=resume_counts(resumes),
        applications=application_counts(category_rows, stages),
    )


async def parse_job_description_v2(payload: v2.JobDescription) -> v2.ParsedJobDescription:
    """Extract role title, seniority and skills from a job description with the AI service."""
    try:
        result = await ai_service.get_ai_service().parse_job_description(payload.job_description)
        return v2.ParsedJobDescription(
            role_title=result.role_title, seniority=result.seniority, skills=result.skills
        )
    except ai_service.AIConfigurationError as e:
        raise JDParseError(str(e)) from e
    except Exception as e:
        logger.error("jd_parse_failed", error=str(e))
        raise JDParseError("Failed to parse job description") from e


# ── v2: applications ──────────────────────────────────────────
@dataclass(frozen=True)
class ApplicationQuery:
    """The application list (and export) filters of one campaign. Empty means "don't filter";
    ``categories`` / ``stages`` match any of their values; ``q``, ``skill`` and
    ``min_experience`` read the extraction snapshot (:class:`ProfileFilters`); ``ids`` restricts
    to these applications (the export of a selection)."""

    campaign_id: uuid.UUID
    q: str | None = None
    categories: Sequence[str] = ()
    stages: Sequence[str] = ()
    min_score: float | None = None
    max_score: float | None = None
    skill: str | None = None
    min_experience: float | None = None
    ids: Sequence[uuid.UUID] = ()
    sort: v2.ApplicationSort = v2.ApplicationSort.score_desc

    def profile(self) -> ProfileFilters:
        return ProfileFilters(
            search=self.q or None, skill=self.skill or None, min_experience=self.min_experience
        )


# Scores sort unscored last in both directions; every order ends with the id, so pages are
# stable. ``name`` is not here: the name is in ingestion's snapshot (see _application_page).
_V2_ORDERS: dict[v2.ApplicationSort, tuple[ColumnElement[Any], ...]] = {
    v2.ApplicationSort.score_desc: (Application.score.desc().nullslast(), Application.id.asc()),
    v2.ApplicationSort.score: (Application.score.asc().nullslast(), Application.id.asc()),
    v2.ApplicationSort.updated_at_desc: (Application.updated_at.desc(), Application.id.asc()),
    v2.ApplicationSort.updated_at: (Application.updated_at.asc(), Application.id.asc()),
}


def _application_clauses(query: ApplicationQuery) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = [Application.campaign_id == query.campaign_id]
    if query.categories:
        clauses.append(Application.category.in_(list(query.categories)))
    if query.stages:
        clauses.append(Application.pipeline_stage.in_(list(query.stages)))
    if query.min_score is not None:
        clauses.append(Application.score >= query.min_score)
    if query.max_score is not None:
        clauses.append(Application.score <= query.max_score)
    if query.ids:
        clauses.append(Application.id == any_(uuid_array(query.ids)))
    return clauses


def _name_order(name: str | None, application_id: uuid.UUID) -> tuple[bool, str, uuid.UUID]:
    """Name ascending (case-insensitive), unnamed last, then the id."""
    return (name is None, (name or "").casefold(), application_id)


async def _application_page(
    db: AsyncSession,
    query: ApplicationQuery,
    profiles: ResumeProfiles,
    *,
    offset: int | None,
    limit: int | None,
) -> tuple[Sequence[Application], int]:
    """The applications matching ``query`` in its order (one page, or all without
    ``offset``/``limit``) and how many match in all.

    Without profile filters and with an order on hiring's columns: a count and the page (2
    queries). Otherwise the matching ``(id, resume_id)`` pairs are read first; ingestion keeps
    those whose snapshot matches the profile filters, and for ``sort=name`` gives the resumes'
    names, by which the pairs are sorted here; then the page is read (at most 4 queries, however
    many rows).
    """
    clauses = _application_clauses(query)
    profile_filters = query.profile()
    by_name = query.sort is v2.ApplicationSort.name
    if not profile_filters.active and not by_name:
        total = await repository.count_where(db, clauses)
        if not total:
            return [], 0
        order = _V2_ORDERS[query.sort]
        page = await repository.page_of_applications(db, clauses, order, offset=offset, limit=limit)
        return page, total

    pairs = await repository.application_resume_pairs(db, clauses)
    if profile_filters.active and pairs:
        matched = set(await profiles.matching([rid for _, rid in pairs], profile_filters))
        pairs = [pair for pair in pairs if pair[1] in matched]
    if not pairs:
        return [], 0
    if not by_name:
        clauses.append(Application.id == any_(uuid_array([aid for aid, _ in pairs])))
        page = await repository.page_of_applications(
            db, clauses, _V2_ORDERS[query.sort], offset=offset, limit=limit
        )
        return page, len(pairs)
    names = await profiles.names([rid for _, rid in pairs])
    pairs.sort(key=lambda pair: _name_order(names.get(pair[1]), pair[0]))
    start = offset or 0
    window = pairs[start : start + limit if limit is not None else None]
    found = {a.id: a for a in await repository.applications_by_id(db, [aid for aid, _ in window])}
    return [found[aid] for aid, _ in window if aid in found], len(pairs)


def _person_ref(application: Application, extracted: Mapping[str, Any] | None) -> v2.PersonRef:
    snapshot = extracted or {}
    return v2.PersonRef(
        id=application.person_id, name=snapshot.get("name"), email=snapshot.get("email")
    )


def application_resource(
    application: Application, extracted: Mapping[str, Any] | None
) -> v2.Application:
    """The v2 ``Application``, given its current resume's extraction (spec §7.1 sources)."""
    profile = _profile(extracted)
    return v2.Application(
        id=application.id,
        campaign_id=application.campaign_id,
        resume_id=application.resume_id,
        person=_person_ref(application, extracted),
        score=application.score,
        score_breakdown=application.score_breakdown,
        category=_category(application.category),
        summary=application.summary,
        strengths=application.strengths or [],
        weaknesses=application.weaknesses or [],
        missing_skills=application.missing_skills or [],
        recommendation=application.recommendation,
        skills=profile["skills"],
        years_of_experience=profile["years_of_experience"],
        pipeline_stage=_stage(application.pipeline_stage),
        notes=application.notes,
        created_at=application.created_at,
        updated_at=application.updated_at,
    )


async def _resource(profiles: ResumeProfiles, application: Application) -> v2.Application:
    snapshots = await _snapshots(profiles, [application])
    return application_resource(application, snapshots.get(application.resume_id))


async def list_applications_page(
    db: AsyncSession,
    scope: AccessScope,
    query: ApplicationQuery,
    offset: int,
    limit: int,
    profiles: ResumeProfiles,
) -> tuple[list[v2.Application], int]:
    """A page of the campaign's applications (404 for an unknown campaign) and how many match:
    at most 6 queries whatever the page size (campaign, at most 4 for the page, extractions)."""
    check_scope(db, scope)
    await _require_campaign(db, scope, query.campaign_id)
    applications, total = await _application_page(db, query, profiles, offset=offset, limit=limit)
    snapshots = await _snapshots(profiles, applications)
    return [application_resource(a, snapshots.get(a.resume_id)) for a in applications], total


async def export_applications_csv(
    db: AsyncSession, scope: AccessScope, query: ApplicationQuery, profiles: ResumeProfiles
) -> str:
    """Every matching application (no paging) as CSV, in the query's order, with the columns
    of :data:`CSV_FIELDS`. 404 for an unknown campaign."""
    check_scope(db, scope)
    await _require_campaign(db, scope, query.campaign_id)
    applications, _ = await _application_page(db, query, profiles, offset=None, limit=None)
    snapshots = await _snapshots(profiles, applications)
    return render_applications_csv((a, snapshots.get(a.resume_id)) for a in applications)


async def _require_application_v2(
    db: AsyncSession, scope: AccessScope, application_id: uuid.UUID
) -> Application:
    application = await repository.get_application_by_id(db, scope, application_id)
    if application is None:
        raise NotFoundError("Application not found")
    return application


async def get_application_v2(
    db: AsyncSession, scope: AccessScope, application_id: uuid.UUID, profiles: ResumeProfiles
) -> v2.Application:
    check_scope(db, scope)
    return await _resource(profiles, await _require_application_v2(db, scope, application_id))


async def update_application_v2(
    db: AsyncSession,
    scope: AccessScope,
    application_id: uuid.UUID,
    payload: v2.ApplicationUpdate,
    profiles: ResumeProfiles,
) -> v2.Application:
    """The recruiter's changes: stage, notes, category override (only the fields sent)."""
    check_scope(db, scope)
    application = await _require_application_v2(db, scope, application_id)
    changes = payload.model_dump(exclude_unset=True, mode="json")
    for name, value in changes.items():
        setattr(application, name, value)
    await repository.commit_and_refresh(db, application)
    logger.info("application_updated", application_id=str(application_id), fields=sorted(changes))
    return await _resource(profiles, application)


async def delete_application_v2(
    db: AsyncSession, scope: AccessScope, application_id: uuid.UUID
) -> DeletedApplication:
    """Delete one application. Flushes but does not commit: the caller deletes its resumes in
    the same transaction and commits."""
    check_scope(db, scope)
    application = await _require_application_v2(db, scope, application_id)
    (deleted,) = await _delete_applications(db, [application])
    logger.info("application_deleted", application_id=str(application_id))
    return deleted


async def bulk_applications(
    db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID, action: v2.BulkAction
) -> tuple[v2.BulkApplicationResult, list[DeletedApplication]]:
    """Apply a bulk action to the campaign's applications that ``action.ids`` names (404 for an
    unknown campaign). Ids that are not applications of this campaign are reported as
    ``not_found`` and nothing is done to them; duplicates count once.

    ``stage`` and ``reject`` (stage ``rejected_manual``) commit. ``delete`` flushes and returns
    the deleted applications: the caller deletes their resumes in the same transaction and
    commits.
    """
    check_scope(db, scope)
    await _require_campaign(db, scope, campaign_id)
    requested = list(dict.fromkeys(action.ids))
    applications = await repository.lock_campaign_applications(db, campaign_id, requested)
    found = {a.id for a in applications}
    deleted: list[DeletedApplication] = []
    if isinstance(action, v2.BulkDelete):
        deleted = await _delete_applications(db, applications)
    else:
        stage = action.stage.value if isinstance(action, v2.BulkStage) else REJECTED_STAGE
        for application in applications:
            application.pipeline_stage = stage
        await repository.commit(db)
    logger.info("bulk_applications", action=action.action, count=len(applications))
    result = v2.BulkApplicationResult(
        action=action.action,
        affected=[aid for aid in requested if aid in found],
        not_found=[aid for aid in requested if aid not in found],
    )
    return result, deleted


# ── v2: people, Home ──────────────────────────────────────────
@dataclass(frozen=True)
class PersonActivity:
    """A Person's applications: how many, and when their latest resume was uploaded."""

    applications: int = 0
    last_applied_at: datetime | None = None


async def people_activity(
    db: AsyncSession, scope: AccessScope, person_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, PersonActivity]:
    """The activity of each of these people (one grouped query): their applications to campaigns
    in ``scope`` only; people without any get an empty :class:`PersonActivity`."""
    check_scope(db, scope)
    rows = await repository.person_activity(db, scope, person_ids)
    return {
        pid: PersonActivity(*rows[pid]) if pid in rows else PersonActivity() for pid in person_ids
    }


async def person_applications(
    db: AsyncSession, scope: AccessScope, person_id: uuid.UUID
) -> list[v2.PersonApplication]:
    """The Person's applications, newest first, with their campaigns (one query). Only those to
    campaigns in ``scope``: the others, with their scores and stages, are left out (spec §5.3,
    §15 Q2)."""
    check_scope(db, scope)
    return [
        v2.PersonApplication(
            id=a.id,
            campaign=_campaign_ref(c),
            resume_id=a.resume_id,
            score=a.score,
            category=_category(a.category),
            pipeline_stage=_stage(a.pipeline_stage),
            created_at=a.created_at,
            updated_at=a.updated_at,
        )
        for a, c in await repository.applications_of_person(db, scope, person_id)
    ]


async def recent_strong_matches(
    db: AsyncSession, scope: AccessScope, limit: int, profiles: ResumeProfiles
) -> list[v2.RecentMatch]:
    """The newest strong-match applications across the campaigns in ``scope`` (two queries)."""
    check_scope(db, scope)
    rows = await repository.recent_applications_in_category(db, scope, STRONG_MATCH, limit)
    snapshots = await _snapshots(profiles, [a for a, _ in rows])
    return [
        v2.RecentMatch(
            id=a.id,
            campaign=_campaign_ref(c),
            person=_person_ref(a, snapshots.get(a.resume_id)),
            score=a.score,
            category=_category(a.category),
            pipeline_stage=_stage(a.pipeline_stage),
            created_at=a.created_at,
        )
        for a, c in rows
    ]


async def active_campaigns(
    db: AsyncSession, scope: AccessScope, limit: int, tallies: ResumeTallies
) -> list[v2.Campaign]:
    """The most recently updated active campaigns in ``scope``, with their counts (three
    queries)."""
    check_scope(db, scope)
    campaigns = await repository.recently_updated_campaigns(
        db, scope, v2.CampaignStatus.active.value, limit
    )
    return await _campaign_resources(db, campaigns, tallies)


async def campaign_refs(
    db: AsyncSession, scope: AccessScope, campaign_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, v2.CampaignRef]:
    """``id -> CampaignRef`` of the campaigns among ``campaign_ids`` that exist in ``scope``."""
    check_scope(db, scope)
    campaigns = await repository.campaigns_by_id(db, scope, campaign_ids)
    return {c.id: _campaign_ref(c) for c in campaigns}


async def application_ids_of(
    db: AsyncSession, scope: AccessScope, owners: Sequence[ResumeOwner]
) -> dict[ResumeOwner, uuid.UUID]:
    """The application of each resolved resume owner (campaign and Person), in one query: the
    application a resume belongs to, for the v2 resume list. Owners without a Person or without
    an application (yet) are left out."""
    check_scope(db, scope)
    pairs = list(
        dict.fromkeys((o.campaign_id, o.person_id) for o in owners if o.person_id is not None)
    )
    found = await repository.application_ids_of(db, scope, pairs)
    return {
        owner: found[(owner.campaign_id, owner.person_id)]
        for owner in owners
        if owner.person_id is not None and (owner.campaign_id, owner.person_id) in found
    }


# ── Upload pipeline ───────────────────────────────────────────
# Called by app.modules.ingestion: the upload endpoint (async) and the Celery tasks (sync). The
# tasks run in worker processes without an event loop and use a synchronous SQLAlchemy Session,
# so these take that Session and never commit: the task writes the resume and the application
# in one transaction and commits it.
async def campaign_exists(db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID) -> bool:
    """Whether the campaign exists in ``scope``: what ingestion's campaign use cases (uploads,
    resumes, retries, the progress stream) check first, so they follow the campaign's client."""
    check_scope(db, scope)
    return await repository.get_campaign(db, scope, campaign_id) is not None


@dataclass(frozen=True)
class CampaignRequirements:
    """What a campaign's candidates are scored against."""

    role: str
    required_skills: list[str]


def get_campaign_requirements(db: Session, campaign_id: uuid.UUID) -> CampaignRequirements | None:
    """The campaign's role and required skills, or ``None`` if it does not exist."""
    campaign = repository.get_campaign_sync(db, campaign_id)
    if not campaign:
        return None
    return CampaignRequirements(role=campaign.role, required_skills=campaign.required_skills or [])


# ── Applications (written by the upload pipeline) ─────────────
def upsert_application(
    db: Session,
    *,
    campaign_id: uuid.UUID,
    person_id: uuid.UUID,
    resume_id: uuid.UUID,
    resume_uploaded_at: datetime,
    scoring: ScoringResult,
    application_id: uuid.UUID | None = None,
) -> Application:
    """Create or update the Person's application to the campaign from one scored resume.

    ``INSERT … ON CONFLICT (campaign_id, person_id) DO UPDATE`` (spec §5.1 step 4, §6.2), so
    concurrent calls for the same pair cannot create two applications. Runs in the caller's
    transaction and never commits.

    **Newer resume wins.** ``resume_uploaded_at`` (timezone-aware) is when ``resume_id`` was
    uploaded; resumes are ordered by ``(uploaded_at, resume_id)``, the same order
    ``talent.service.resolve_person`` uses for the profile. When the application already exists:

    - a resume that is newer than the one it holds, **or the same resume** (a retried pipeline
      step), replaces ``resume_id``, ``resume_uploaded_at`` and every scoring and insight field;
    - an older resume (processed late) changes nothing: the newer scoring stays;
    - an application whose resume upload time is unknown (``NULL``, written before E0-16) counts
      as older than any resume.

    The recruiter's fields (``pipeline_stage``, ``notes``) are never written here, and an
    existing application keeps its ``id``.

    ``application_id`` is the id for a **new** row (the seed generator passes ids drawn from its
    own random source, so its data is reproducible); it is ignored when the application already
    exists. Default: a new UUID.

    Returns the application as stored after the call.
    """
    if resume_uploaded_at.tzinfo is None:
        raise ValueError("resume_uploaded_at must be timezone-aware")
    values: dict[str, Any] = {
        "id": application_id or uuid.uuid4(),
        # Inserted only: an existing row keeps its agency (one written before E2-01 keeps NULL
        # until the E2-02 backfill).
        "agency_id": agency_id_of(db),
        "campaign_id": campaign_id,
        "person_id": person_id,
        "resume_id": resume_id,
        "resume_uploaded_at": resume_uploaded_at,
        "score": scoring.score,
        "score_breakdown": scoring.score_breakdown,
        "category": scoring.category,
        "summary": scoring.summary,
        "strengths": scoring.strengths,
        "weaknesses": scoring.weaknesses,
        "missing_skills": scoring.missing_skills,
        "recommendation": scoring.recommendation,
    }
    application = repository.upsert_application_sync(db, values)
    logger.info(
        "application_upserted",
        application_id=str(application.id),
        campaign_id=str(campaign_id),
        person_id=str(person_id),
        resume_id=str(resume_id),
        scoring_replaced=application.resume_id == resume_id,
    )
    return application


# ── Verification (``app.modules.ingestion.verify_people_applications``, spec §6.3) ──
@dataclass(frozen=True)
class ApplicationRef:
    id: uuid.UUID
    campaign_id: uuid.UUID
    person_id: uuid.UUID
    resume_id: uuid.UUID
    agency_id: uuid.UUID | None


def application_refs(db: Session) -> list[ApplicationRef]:
    """Every application."""
    return [ApplicationRef(**row._asdict()) for row in repository.application_refs_sync(db)]


@dataclass(frozen=True)
class CampaignTenancy:
    agency_id: uuid.UUID | None
    client_id: uuid.UUID | None


def campaign_tenancy(db: Session) -> dict[uuid.UUID, CampaignTenancy]:
    """``campaign_id -> (agency_id, client_id)`` of every campaign."""
    return {
        row.id: CampaignTenancy(agency_id=row.agency_id, client_id=row.client_id)
        for row in repository.campaign_tenancy_sync(db)
    }


def client_agencies(db: Session) -> dict[uuid.UUID, uuid.UUID]:
    """``client_id -> agency_id`` of every client."""
    return repository.client_agencies_sync(db)


def agency_census(db: Session) -> dict[str, dict[uuid.UUID | None, int]]:
    """``table -> {agency_id: rows}`` for ``campaigns`` and ``applications`` (``None`` counts
    the rows without an agency)."""
    return repository.agency_census_sync(db)


# ── Tenancy backfill (``app.modules.ingestion.backfill_tenancy``, E2-02) ──
TENANCY_TABLES: tuple[str, ...] = repository.TENANCY_TABLES


def fill_tenancy_batch(
    db: Session,
    table: str,
    *,
    agency_id: uuid.UUID,
    client_id: uuid.UUID,
    after: uuid.UUID | None,
    limit: int,
) -> list[uuid.UUID]:
    """One backfill batch of ``table`` (one of :data:`TENANCY_TABLES`): the next ``limit`` rows
    after ``after`` (by id) without an agency get ``agency_id``, and campaigns of that agency
    without a client get ``client_id``. ``updated_at`` is left alone. Returns the ids updated,
    ascending (empty: the table is done after ``after``). Does not commit."""
    return repository.fill_tenancy_batch_sync(
        db, table, agency_id=agency_id, client_id=client_id, after=after, limit=limit
    )


def count_tenancy_gaps(db: Session, table: str) -> int:
    """Rows of ``table`` still without an agency (campaigns: or without a client)."""
    return repository.count_tenancy_gaps_sync(db, table)
