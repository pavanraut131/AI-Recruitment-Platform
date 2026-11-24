"""hiring service: the public interface of the hiring module.

Campaign CRUD and stats, the global stats, job description parsing, and the legacy candidate
endpoints' use cases (list, export, detail, recruiter updates, deletes), served from
``applications`` since the read switch (E0-19). The routers in ``router.py`` only translate HTTP
to these calls; other modules (``ingestion``) and the composition layer (``app.api``) may import
this module and ``app.modules.hiring.schemas``, never ``models`` or ``repository``
(import-linter enforces it).

Business rules live here: the category / pipeline-stage vocabularies, the candidate filters
shared by the list and the CSV export, the legacy response mapping and the stats aggregation.
Job descriptions are parsed by the ``ai`` module. Errors are raised as :class:`HiringError`
subclasses carrying the user-facing ``detail``; the routers map them to status codes.

The ``/api/v2`` use cases (E1-02, EPIC-01 spec §6) are in the section "/api/v2 resources": they
return the resources of ``schemas_v2`` and take ingestion's data through the same kind of ports
(:class:`ResumeProfiles`, :class:`ResumeTallies`), passed in by ``app.api.v2``.

The legacy candidate API after the switch (E0-19, spec §7)
---------------------------------------------------------
The ``/candidates`` endpoints keep their paths and exact response shapes, but a "candidate" is
now an **application**: one row per (campaign, person). A response is built by
:func:`candidate_detail` / :func:`candidate_list_item` from two sources, field by field (the
table is in spec §7):

- the **application** (hiring): ``id``, ``campaign_id``, ``resume_id``, the scoring and
  insights, ``category``, ``notes``, ``pipeline_stage``, ``created_at``, ``updated_at``;
- the **extraction snapshot of the application's current resume** (``resumes.extracted``,
  ingestion): name, contact details, URLs, skills, education, work experience,
  certifications, projects and years of experience. These are per-resume facts, exactly what
  the legacy candidate of that resume held. The Person's profile is deliberately not used: it
  follows the person's newest resume across *all* campaigns, so it can differ from what this
  application was scored on.

Ids: an application id answers directly; the id of a legacy candidate that was merged into
another application (``legacy_candidate_map``) answers with that application, and the response
carries the **application's** id. Applications created after the switch get fresh ids and have
no legacy candidate.

Deletes remove the application and every resume of its person in its campaign (each was one
legacy candidate of the row the recruiter sees), and the resumes' stored PDFs. The legacy
``candidates`` table itself was dropped by the contract migration (E0-20).

Cross-module boundaries (E0-10)
-------------------------------
Hiring never reads or writes another module's tables. Where a use case needs another module,
the data crosses this interface instead:

1. **Stats** (``GET /campaigns/{id}``, ``GET /campaigns/{id}/stats``, ``GET /stats``) need
   resume counts by status, which ingestion owns. :func:`campaign_stats` and
   :func:`global_stats` take them as an argument; ``app.api`` asks ingestion for them and
   composes the endpoint.
2. **Resume extractions** (E0-19): every candidate endpoint, and the campaign stats' top skills,
   read the resumes' extraction snapshots. They take a :class:`ResumeProfiles` (ingestion's
   ``resume_profiles(db)``), which answers with the snapshots, the resumes matching the list's
   name/email search, skill and experience filters, and a resume's campaign and person.
3. **Deleting candidates** (``DELETE /candidates/{id}``, ``DELETE
   /campaigns/{id}/candidates/bulk``) also deletes resumes. :func:`delete_candidate` and
   :func:`bulk_delete_candidates` delete the applications (flush, don't commit) and return
   what ingestion needs to delete their resumes; ``app.api`` has ingestion delete them and
   commits both in one transaction, then has ingestion delete the stored PDFs.
4. **Deleting a campaign** (E0-20): :func:`delete_campaign` deletes the campaign and, by the
   same-module foreign key, its applications (flush, don't commit); ``app.api`` has ingestion
   delete the campaign's resumes in the same transaction and their PDFs after the commit. No
   foreign key links ``resumes`` to ``campaigns`` any more (ADR 0003).
5. **The upload pipeline** (``app.modules.ingestion``) checks campaigns through
   :func:`get_campaign_requirements` and writes applications through :func:`upsert_application`.
   Its scoring input is the ``ai`` module's ``ScoringResult`` (``app.modules.ai.schemas``,
   public): the pipeline passes on what the model returned, and hiring keeps no second copy of
   that shape to keep in sync. The verification command
   (``app.modules.ingestion.verify_people_applications``) reads applications through
   :func:`application_refs` and :func:`legacy_candidate_map`.

Tenancy (EPIC-02)
-----------------
The session is bound to an agency (``app.platform.tenancy``, E2-03): the repository filters
every query to it, and every insert takes its ``agency_id`` from it (:func:`_new_campaign`,
:func:`upsert_application`). A new campaign is run for the default client until E2-06 makes the
client a field of the request.

The use cases that list or fetch campaign data take an ``AccessScope`` (spec §5.3, §5.4), which
:func:`check_scope` checks against the session's agency. Every scope covers all clients until
client scoping arrives (E2-06); a narrower one is refused rather than silently widened.

Rows written before E2-01 get their agency from the tenancy backfill
(``app.modules.ingestion.backfill_tenancy``, E2-02) through :func:`fill_tenancy_batch`; it and
the verification's reads (:func:`application_refs`, :func:`campaign_tenancy`,
:func:`client_agencies`, :func:`agency_census`) read **every agency** on purpose (operator
commands, unbound sessions).
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
from app.modules.hiring.models import Application, Campaign
from app.modules.hiring.schemas import (
    CampaignCreate,
    CampaignResponse,
    CampaignStats,
    CampaignUpdate,
    CandidateDetail,
    CandidateListItem,
    GlobalStats,
    JDParseRequest,
    JDParseResponse,
)
from app.platform.sql import LIKE_ESCAPE, escape_like, uuid_array
from app.platform.tenancy import DEFAULT_CLIENT_ID, AccessScope, agency_id_of, require_scope

logger = structlog.get_logger()

VALID_CATEGORIES = {"strong_match", "moderate_match", "weak_match", "rejected"}
VALID_PIPELINE_STAGES = {"screened", "phone_call", "technical", "offer", "hired", "rejected_manual"}

PROCESSING_STATUSES = ("pending", "extracting", "processing")
TERMINAL_FAILED_STATUSES = ("failed", "quota_exceeded")


# ── Errors ────────────────────────────────────────────────────
class HiringError(Exception):
    """A hiring operation failed; ``detail`` is the message returned to the client."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFoundError(HiringError):
    """The campaign or candidate does not exist."""


class InvalidInputError(HiringError):
    """The request names a category or pipeline stage outside the allowed vocabulary."""


class JDParseError(HiringError):
    """The job description could not be parsed (AI provider not configured, or the call or its
    answer failed)."""


# ── Tenancy ───────────────────────────────────────────────────
def check_scope(db: AsyncSession, scope: AccessScope) -> None:
    """``scope`` must be of the session's agency. Client scoping (spec §5.3) arrives with
    E2-06: until then a scope limited to some clients is refused, never widened to all."""
    require_scope(db, scope)
    if not scope.all_clients:
        raise NotImplementedError("client-scoped access arrives with E2-06")


# ── Campaigns ─────────────────────────────────────────────────
def _new_campaign(db: AsyncSession, fields: Mapping[str, Any]) -> Campaign:
    """A campaign to insert, in the session's agency, run for the default client (the client
    comes from the request from E2-06)."""
    return Campaign(**fields, agency_id=agency_id_of(db), client_id=DEFAULT_CLIENT_ID)


async def create_campaign(
    db: AsyncSession, scope: AccessScope, payload: CampaignCreate
) -> Campaign:
    check_scope(db, scope)
    campaign = await repository.add_campaign(db, _new_campaign(db, payload.model_dump()))
    logger.info("campaign_created", id=str(campaign.id), title=campaign.title)
    return campaign


async def list_campaigns(
    db: AsyncSession, scope: AccessScope, skip: int, limit: int
) -> Sequence[Campaign]:
    check_scope(db, scope)
    return await repository.list_campaigns(db, skip, limit)


async def _require_campaign(db: AsyncSession, campaign_id: uuid.UUID) -> Campaign:
    campaign = await repository.get_campaign(db, campaign_id)
    if not campaign:
        raise NotFoundError("Campaign not found")
    return campaign


async def get_campaign(
    db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID
) -> CampaignResponse:
    """The campaign, without stats (``stats`` is ``None``; see :func:`campaign_stats`)."""
    check_scope(db, scope)
    campaign = await _require_campaign(db, campaign_id)
    return CampaignResponse.model_validate(campaign)


async def update_campaign(
    db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID, payload: CampaignUpdate
) -> Campaign:
    """Apply the fields the client sent (unset fields are left alone)."""
    check_scope(db, scope)
    campaign = await _require_campaign(db, campaign_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(campaign, field, value)
    await repository.commit_and_refresh(db, campaign)
    return campaign


async def delete_campaign(db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID) -> None:
    """Delete the campaign and its applications (``applications.campaign_id ON DELETE
    CASCADE``; their ``legacy_candidate_map`` rows go with them).

    Flushes but does not commit: the caller (``app.api``) has ingestion delete the campaign's
    resumes in the same transaction and commits. Raises :class:`NotFoundError` for an unknown
    campaign.
    """
    check_scope(db, scope)
    campaign = await _require_campaign(db, campaign_id)
    await repository.delete_campaign(db, campaign)
    logger.info("campaign_deleted", id=str(campaign_id))


# ── Stats ─────────────────────────────────────────────────────
async def campaign_stats(
    db: AsyncSession,
    scope: AccessScope,
    campaign_id: uuid.UUID,
    resume_counts: Mapping[str, int],
    profiles: "ResumeProfiles",
) -> CampaignStats:
    """Stats for one campaign, given its resume counts by status and its resumes' extractions
    (both from ingestion).

    The category counts and average scores are over the campaign's applications; the top skills
    over the skills extracted from each application's current resume (what the legacy candidate
    of that resume held), taken oldest application first so equal counts keep a stable order.
    An unknown campaign id yields all-zero stats, not a 404.
    """
    check_scope(db, scope)
    category_rows = await repository.application_category_stats(db, campaign_id)
    resume_ids = await repository.campaign_resume_ids(db, campaign_id)
    skills = await profiles.skill_lists(resume_ids) if resume_ids else {}
    return build_campaign_stats(
        resume_counts, category_rows, (skills.get(rid) for rid in resume_ids)
    )


def build_campaign_stats(
    resume_counts: Mapping[str, int],
    category_rows: Iterable[tuple[str | None, int, float | None]],
    skill_lists: Iterable[list[str] | None],
) -> CampaignStats:
    """Aggregate a campaign's resume counts by status, application ``(category, count, average
    score)`` rows and the applications' skill lists into :class:`CampaignStats`."""
    cat_map: dict[str | None, int] = {}
    avg_scores: list[float] = []
    for category, count, avg in category_rows:
        cat_map[category] = count
        if avg:
            avg_scores.append(avg)

    # Top skills distribution
    skill_counts: dict[str, int] = {}
    for skills in skill_lists:
        for skill in skills or []:
            skill_counts[skill] = skill_counts.get(skill, 0) + 1

    # Most frequent first; ties keep first-seen order (a reverse sort is still stable).
    ranked = sorted(skill_counts.items(), key=lambda item: item[1], reverse=True)[:10]
    top_skills: list[dict[str, Any]] = [{"skill": k, "count": v} for k, v in ranked]

    total = sum(resume_counts.values())
    quota_exceeded = resume_counts.get("quota_exceeded", 0)
    return CampaignStats(
        total_resumes=total,
        total_processed=resume_counts.get("done", 0),
        total_failed=resume_counts.get("failed", 0),
        total_pending=resume_counts.get("pending", 0)
        + resume_counts.get("extracting", 0)
        + resume_counts.get("processing", 0),
        total_quota_exceeded=quota_exceeded,
        avg_score=round(sum(avg_scores) / len(avg_scores), 2) if avg_scores else None,
        strong_match=cat_map.get("strong_match", 0),
        moderate_match=cat_map.get("moderate_match", 0),
        weak_match=cat_map.get("weak_match", 0),
        rejected=cat_map.get("rejected", 0),
        top_skills=top_skills,
    )


async def global_stats(
    db: AsyncSession, scope: AccessScope, resume_counts: Mapping[str, int]
) -> GlobalStats:
    """Aggregate counts across all campaigns, given the resume counts by status (from
    ingestion): single-table queries, no joins. ``total_candidates`` counts applications."""
    check_scope(db, scope)
    campaign_counts = await repository.campaign_status_counts(db)
    total_candidates = await repository.count_applications(db)
    return build_global_stats(campaign_counts, resume_counts, total_candidates)


def build_global_stats(
    campaign_counts: Mapping[str, int],
    resume_counts: Mapping[str, int],
    total_candidates: int,
) -> GlobalStats:
    """Aggregate campaign and resume counts by status into :class:`GlobalStats`."""
    return GlobalStats(
        total_campaigns=sum(campaign_counts.values()),
        active_campaigns=campaign_counts.get("active", 0),
        closed_campaigns=campaign_counts.get("closed", 0),
        archived_campaigns=campaign_counts.get("archived", 0),
        total_resumes=sum(resume_counts.values()),
        total_processed=resume_counts.get("done", 0),
        total_processing=sum(resume_counts.get(s, 0) for s in PROCESSING_STATUSES),
        total_failed=sum(resume_counts.get(s, 0) for s in TERMINAL_FAILED_STATUSES),
        total_candidates=total_candidates,
    )


# ── Job description parsing ───────────────────────────────────
async def parse_job_description(payload: JDParseRequest) -> JDParseResponse:
    """Extract role title, seniority and skills from a job description with the AI service."""
    try:
        result = await ai_service.get_ai_service().parse_job_description(payload.job_description)
        return JDParseResponse(
            role_title=result.role_title, seniority=result.seniority, skills=result.skills
        )
    except ai_service.AIConfigurationError as e:
        raise JDParseError(str(e)) from e
    except Exception as e:
        logger.error("jd_parse_failed", error=str(e))
        raise JDParseError("Failed to parse job description") from e


# ── Candidate filters (shared by the list and the CSV export) ──
@dataclass(frozen=True)
class CandidateFilters:
    """The candidate list filters. ``None`` (or an empty string) means "don't filter"."""

    campaign_id: uuid.UUID
    skill: str | None = None
    category: str | None = None
    min_score: float | None = None
    max_score: float | None = None
    min_experience: float | None = None
    search: str | None = None
    pipeline_stage: str | None = None

    def profile(self) -> "ProfileFilters":
        """The filters on the resume's extracted profile, which ingestion evaluates."""
        return ProfileFilters(
            search=self.search or None,
            skill=self.skill or None,
            min_experience=self.min_experience,
        )


@dataclass(frozen=True)
class ProfileFilters:
    """The list filters that read a resume's extraction snapshot (``resumes.extracted``), with
    the legacy semantics (B2): ``search`` is contained in the name or the email, ``skill`` in
    any one skill, both case-insensitively and with LIKE wildcards taken literally;
    ``min_experience`` is inclusive and never matches an unknown experience."""

    search: str | None = None
    skill: str | None = None
    min_experience: float | None = None

    @property
    def active(self) -> bool:
        return bool(self.search) or bool(self.skill) or self.min_experience is not None


def build_application_filters(filters: CandidateFilters) -> list[ColumnElement[bool]]:
    """The SQL ``WHERE`` clauses (to be AND-ed) on ``applications`` for ``filters`` (the profile
    filters are not among them: see :meth:`CandidateFilters.profile`)."""
    clauses: list[ColumnElement[bool]] = [Application.campaign_id == filters.campaign_id]
    if filters.category:
        clauses.append(Application.category == filters.category)
    if filters.min_score is not None:
        clauses.append(Application.score >= filters.min_score)
    if filters.max_score is not None:
        clauses.append(Application.score <= filters.max_score)
    if filters.pipeline_stage:
        clauses.append(Application.pipeline_stage == filters.pipeline_stage)
    return clauses


# ── Resume extractions (ingestion's data, E0-19) ──────────────
@dataclass(frozen=True)
class ResumeOwner:
    """The campaign a resume was uploaded to and the Person it resolved to (``None`` until it
    is resolved)."""

    campaign_id: uuid.UUID
    person_id: uuid.UUID | None


class ResumeProfiles(Protocol):
    """What the candidate endpoints need from ingestion's ``resumes`` (``app.api`` passes
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

    async def owner(self, resume_id: uuid.UUID) -> ResumeOwner | None:
        """The resume's campaign and Person, or ``None`` if it does not exist."""
        ...

    async def names(self, resume_ids: Sequence[uuid.UUID]) -> Mapping[uuid.UUID, str | None]:
        """The extracted ``name`` of each resume (the v2 list's ``sort=name``)."""
        ...


# ── The legacy response, field by field (spec §7) ─────────────
# From the extraction snapshot of the application's current resume: what the legacy candidate
# of that resume held (the pre-switch pipeline copied the same ``ExtractionResult`` onto it).
# A missing snapshot reads like the legacy placeholder row: no values, empty lists.
_SNAPSHOT_VALUES = ("name", "email", "phone", "github_url", "linkedin_url")
_SNAPSHOT_LISTS = ("skills", "education", "work_experience", "certifications", "projects")


def _profile(extracted: Mapping[str, Any] | None) -> dict[str, Any]:
    snapshot = extracted or {}
    profile: dict[str, Any] = {name: snapshot.get(name) for name in _SNAPSHOT_VALUES}
    for name in _SNAPSHOT_LISTS:
        value = snapshot.get(name)
        profile[name] = value if value is not None else []
    years = snapshot.get("years_of_experience")
    # A float column on the legacy row; JSON may hold it as an integer.
    profile["years_of_experience"] = float(years) if years is not None else None
    return profile


def candidate_detail(
    application: Application, extracted: Mapping[str, Any] | None
) -> CandidateDetail:
    """The legacy ``CandidateDetail`` of an application, given its resume's extraction."""
    return CandidateDetail(
        id=application.id,
        resume_id=application.resume_id,
        campaign_id=application.campaign_id,
        **_profile(extracted),
        summary=application.summary,
        strengths=application.strengths,
        weaknesses=application.weaknesses,
        missing_skills=application.missing_skills,
        recommendation=application.recommendation,
        score=application.score,
        category=application.category,
        notes=application.notes,
        pipeline_stage=application.pipeline_stage,
        score_breakdown=application.score_breakdown,
        created_at=application.created_at,
        updated_at=application.updated_at,
    )


def candidate_list_item(
    application: Application, extracted: Mapping[str, Any] | None
) -> CandidateListItem:
    """The legacy ``CandidateListItem`` of an application, given its resume's extraction."""
    profile = _profile(extracted)
    return CandidateListItem(
        id=application.id,
        resume_id=application.resume_id,
        campaign_id=application.campaign_id,
        name=profile["name"],
        email=profile["email"],
        skills=profile["skills"],
        years_of_experience=profile["years_of_experience"],
        score=application.score,
        category=application.category,
        summary=application.summary,
        pipeline_stage=application.pipeline_stage,
        created_at=application.created_at,
    )


async def _snapshots(
    profiles: ResumeProfiles, applications: Sequence[Application]
) -> Mapping[uuid.UUID, Mapping[str, Any] | None]:
    """The extraction of every application's current resume, in one lookup (no N+1)."""
    if not applications:
        return {}
    return await profiles.extractions([a.resume_id for a in applications])


async def _detail(profiles: ResumeProfiles, application: Application) -> CandidateDetail:
    snapshots = await _snapshots(profiles, [application])
    return candidate_detail(application, snapshots.get(application.resume_id))


# ── Candidates ────────────────────────────────────────────────
async def _find_applications(
    db: AsyncSession,
    filters: CandidateFilters,
    profiles: ResumeProfiles,
    *,
    offset: int | None = None,
    limit: int | None = None,
) -> Sequence[Application]:
    """The applications matching ``filters``, best score first (unscored last).

    The application filters run in hiring's query. The profile filters (search, skill,
    experience) read ingestion's extractions: first the resume ids of the applications that
    pass the application filters, then ingestion keeps those whose extraction matches, then the
    page is read restricted to them. Without profile filters that is one query.
    """
    clauses = build_application_filters(filters)
    profile_filters = filters.profile()
    if profile_filters.active:
        resume_ids = await repository.application_resume_ids(db, clauses)
        matched = await profiles.matching(resume_ids, profile_filters) if resume_ids else ()
        if not matched:
            return []
        clauses.append(Application.resume_id == any_(uuid_array(matched)))
    return await repository.list_applications(db, clauses, offset=offset, limit=limit)


async def list_candidates(
    db: AsyncSession,
    scope: AccessScope,
    filters: CandidateFilters,
    page: int,
    page_size: int,
    profiles: ResumeProfiles,
) -> list[CandidateListItem]:
    """One page of matching candidates (applications), best score first (unscored last)."""
    check_scope(db, scope)
    applications = await _find_applications(
        db, filters, profiles, offset=(page - 1) * page_size, limit=page_size
    )
    snapshots = await _snapshots(profiles, applications)
    return [candidate_list_item(a, snapshots.get(a.resume_id)) for a in applications]


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


async def export_candidates_csv(
    db: AsyncSession, scope: AccessScope, filters: CandidateFilters, profiles: ResumeProfiles
) -> str:
    """All matching candidates (no paging) as a CSV document, best score first."""
    check_scope(db, scope)
    applications = await _find_applications(db, filters, profiles)
    snapshots = await _snapshots(profiles, applications)
    return render_candidates_csv(
        candidate_detail(a, snapshots.get(a.resume_id)) for a in applications
    )


def _join(values: Sequence[Any] | None) -> str:
    return " | ".join(str(v) for v in (values or []))


def render_candidates_csv(candidates: Iterable[CandidateDetail]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for c in candidates:
        writer.writerow(
            {
                "Name": c.name or "",
                "Email": c.email or "",
                "Phone": c.phone or "",
                "Score": f"{c.score:.0f}" if c.score is not None else "",
                "Category": c.category or "",
                "Pipeline Stage": c.pipeline_stage or "",
                "Years Experience": f"{c.years_of_experience:.1f}"
                if c.years_of_experience is not None
                else "",
                "Skills": _join(c.skills),
                "Missing Skills": _join(c.missing_skills),
                "Strengths": _join(c.strengths),
                "Recommendation": c.recommendation or "",
                "LinkedIn URL": c.linkedin_url or "",
                "GitHub URL": c.github_url or "",
            }
        )
    return output.getvalue()


async def _require_application(db: AsyncSession, candidate_id: uuid.UUID) -> Application:
    """The application ``candidate_id`` names: its own id, or a merged legacy candidate's id
    redirected by ``legacy_candidate_map``."""
    application = await repository.get_application(db, candidate_id)
    if not application:
        raise NotFoundError("Candidate not found")
    return application


async def get_candidate(
    db: AsyncSession, scope: AccessScope, candidate_id: uuid.UUID, profiles: ResumeProfiles
) -> CandidateDetail:
    """The candidate (application) with this id, or the application a merged legacy candidate
    with this id was redirected to; the response carries the application's id."""
    check_scope(db, scope)
    return await _detail(profiles, await _require_application(db, candidate_id))


async def get_candidate_by_resume(
    db: AsyncSession, scope: AccessScope, resume_id: uuid.UUID, profiles: ResumeProfiles
) -> CandidateDetail:
    """The application a resume belongs to: the one it is the current resume of, else (an
    older resume of a merged application) the application of its Person in its campaign.
    404 while the resume is not processed yet (no application)."""
    check_scope(db, scope)
    application = await repository.get_application_by_resume(db, resume_id)
    if application is None:
        owner = await profiles.owner(resume_id)
        if owner is not None and owner.person_id is not None:
            application = await repository.get_application_of_person(
                db, owner.campaign_id, owner.person_id
            )
    if application is None:
        raise NotFoundError("Candidate not yet processed")
    return await _detail(profiles, application)


def _check_category(category: str) -> None:
    if category not in VALID_CATEGORIES:
        raise InvalidInputError(
            f"Invalid category. Must be one of: {', '.join(VALID_CATEGORIES)}",
        )


def _check_pipeline_stage(stage: str) -> None:
    if stage not in VALID_PIPELINE_STAGES:
        raise InvalidInputError(
            f"Invalid stage. Must be one of: {', '.join(sorted(VALID_PIPELINE_STAGES))}",
        )


async def update_candidate_category(
    db: AsyncSession,
    scope: AccessScope,
    candidate_id: uuid.UUID,
    category: str,
    profiles: ResumeProfiles,
) -> CandidateDetail:
    """Recruiter override of the AI category. The value is checked before the lookup."""
    check_scope(db, scope)
    _check_category(category)
    application = await _require_application(db, candidate_id)
    application.category = category
    await repository.commit_and_refresh(db, application)
    logger.info(
        "category_overridden",
        candidate_id=str(candidate_id),
        application_id=str(application.id),
        category=category,
    )
    return await _detail(profiles, application)


async def update_candidate_notes(
    db: AsyncSession,
    scope: AccessScope,
    candidate_id: uuid.UUID,
    notes: str,
    profiles: ResumeProfiles,
) -> CandidateDetail:
    check_scope(db, scope)
    application = await _require_application(db, candidate_id)
    application.notes = notes
    await repository.commit_and_refresh(db, application)
    logger.info("notes_updated", candidate_id=str(candidate_id), application_id=str(application.id))
    return await _detail(profiles, application)


async def update_candidate_pipeline(
    db: AsyncSession,
    scope: AccessScope,
    candidate_id: uuid.UUID,
    stage: str,
    profiles: ResumeProfiles,
) -> CandidateDetail:
    """Move one candidate to a pipeline stage. The stage is checked before the lookup."""
    check_scope(db, scope)
    _check_pipeline_stage(stage)
    application = await _require_application(db, candidate_id)
    application.pipeline_stage = stage
    await repository.commit_and_refresh(db, application)
    logger.info(
        "pipeline_updated",
        candidate_id=str(candidate_id),
        application_id=str(application.id),
        stage=stage,
    )
    return await _detail(profiles, application)


async def bulk_update_pipeline(
    db: AsyncSession,
    scope: AccessScope,
    campaign_id: uuid.UUID,
    candidate_ids: Sequence[uuid.UUID],
    stage: str,
) -> int:
    """Move the campaign's candidates (applications) that ``candidate_ids`` name to ``stage``;
    returns how many applications."""
    check_scope(db, scope)
    _check_pipeline_stage(stage)
    applications = await repository.applications_in_campaign(
        db, campaign_id, candidate_ids, for_update=True
    )
    for application in applications:
        application.pipeline_stage = stage
    await repository.commit(db)
    logger.info("bulk_pipeline_updated", count=len(applications), stage=stage)
    return len(applications)


@dataclass(frozen=True)
class DeletedApplication:
    """A deleted application: ingestion deletes its resume and every other resume of its Person
    in its campaign (``ingestion.service.delete_application_resumes``)."""

    id: uuid.UUID
    campaign_id: uuid.UUID
    person_id: uuid.UUID
    resume_id: uuid.UUID


async def bulk_delete_candidates(
    db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID, candidate_ids: Sequence[uuid.UUID]
) -> list[DeletedApplication]:
    """Delete the campaign's candidates (applications) that ``candidate_ids`` name (others are
    ignored).

    Flushes but does not commit: the caller deletes their resumes in the same transaction and
    commits.
    """
    check_scope(db, scope)
    applications = await repository.applications_in_campaign(
        db, campaign_id, candidate_ids, for_update=True
    )
    deleted = await _delete_applications(db, applications)
    logger.info("bulk_candidates_deleted", count=len(deleted))
    return deleted


async def delete_candidate(
    db: AsyncSession, scope: AccessScope, candidate_id: uuid.UUID
) -> DeletedApplication:
    """Delete one candidate (application). Flushes but does not commit: the caller deletes its
    resumes in the same transaction and commits."""
    check_scope(db, scope)
    application = await _require_application(db, candidate_id)
    (deleted,) = await _delete_applications(db, [application])
    logger.info("candidate_deleted", candidate_id=str(candidate_id), application_id=str(deleted.id))
    return deleted


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
# The v2 use cases return the resources of ``schemas_v2``. They share the rules above (filters on
# the extraction snapshot, field sources, deletes) and differ from the legacy ones where v2
# fixes a recorded bug: unknown campaigns are a 404 (B7), the export applies every list filter
# (B8), ``avg_score`` is the mean over the scored applications (B9), and invalid values are
# rejected by the request schema (422) before they get here (B5, B10). Ids are application ids
# only: v2 does not resolve merged legacy candidate ids (``legacy_candidate_map``), which only
# the v1 compatibility layer needs.
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
    """The resume tallies of several campaigns (``app.api`` passes
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
    """A page of campaigns, newest first, and how many match: four queries."""
    check_scope(db, scope)
    campaigns, total = await repository.find_campaigns(
        db, _campaign_clauses(query), offset=offset, limit=limit
    )
    return await _campaign_resources(db, campaigns, tallies), total


async def create_campaign_v2(
    db: AsyncSession, scope: AccessScope, payload: v2.CampaignCreate
) -> v2.Campaign:
    """Create a campaign. A new campaign has no resumes or applications yet, so its counts are
    zero without asking ingestion."""
    check_scope(db, scope)
    campaign = await repository.add_campaign(db, _new_campaign(db, payload.model_dump(mode="json")))
    logger.info("campaign_created", id=str(campaign.id), title=campaign.title)
    return campaign_resource(campaign, None, ResumeTally())


async def get_campaign_v2(
    db: AsyncSession, scope: AccessScope, campaign_id: uuid.UUID, tallies: ResumeTallies
) -> v2.Campaign:
    check_scope(db, scope)
    (resource,) = await _campaign_resources(db, [await _require_campaign(db, campaign_id)], tallies)
    return resource


async def update_campaign_v2(
    db: AsyncSession,
    scope: AccessScope,
    campaign_id: uuid.UUID,
    payload: v2.CampaignUpdate,
    tallies: ResumeTallies,
) -> v2.Campaign:
    """Apply the fields the client sent (unset fields are left alone)."""
    check_scope(db, scope)
    campaign = await _require_campaign(db, campaign_id)
    changes = payload.model_dump(exclude_unset=True, mode="json")
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
    campaign = await _require_campaign(db, campaign_id)
    category_rows = await repository.category_score_rows(db, campaign_id)
    stages = await repository.stage_counts(db, campaign_id)
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
    """The mean over every scored application, to 2 decimals (the v1 ``avg_score`` is a mean of
    per-category means that also drops a 0 mean: B9)."""
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
    """Counts across every campaign, given every resume's tally (from ingestion): three
    single-table queries."""
    check_scope(db, scope)
    campaign_counts = await repository.campaign_status_counts(db)
    category_rows = await repository.category_score_rows(db)
    stages = await repository.stage_counts(db)
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
    parsed = await parse_job_description(JDParseRequest(job_description=payload.job_description))
    return v2.ParsedJobDescription(
        role_title=parsed.role_title, seniority=parsed.seniority, skills=parsed.skills
    )


# ── v2: applications ──────────────────────────────────────────
@dataclass(frozen=True)
class ApplicationQuery:
    """The application list (and export) filters of one campaign. Empty means "don't filter";
    ``categories`` / ``stages`` match any of their values; ``q``, ``skill`` and
    ``min_experience`` read the extraction snapshot, as the legacy ``search`` / ``skill`` /
    ``min_experience`` do (:class:`ProfileFilters`); ``ids`` restricts to these applications
    (the export of a selection)."""

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
    await _require_campaign(db, query.campaign_id)
    applications, total = await _application_page(db, query, profiles, offset=offset, limit=limit)
    snapshots = await _snapshots(profiles, applications)
    return [application_resource(a, snapshots.get(a.resume_id)) for a in applications], total


async def export_applications_csv(
    db: AsyncSession, scope: AccessScope, query: ApplicationQuery, profiles: ResumeProfiles
) -> str:
    """Every matching application (no paging) as CSV, in the query's order, with the columns
    of the legacy export (:data:`CSV_FIELDS`). 404 for an unknown campaign."""
    check_scope(db, scope)
    await _require_campaign(db, query.campaign_id)
    applications, _ = await _application_page(db, query, profiles, offset=None, limit=None)
    snapshots = await _snapshots(profiles, applications)
    return render_candidates_csv(
        candidate_detail(a, snapshots.get(a.resume_id)) for a in applications
    )


async def _require_application_v2(db: AsyncSession, application_id: uuid.UUID) -> Application:
    application = await repository.get_application_by_id(db, application_id)
    if application is None:
        raise NotFoundError("Application not found")
    return application


async def get_application_v2(
    db: AsyncSession, scope: AccessScope, application_id: uuid.UUID, profiles: ResumeProfiles
) -> v2.Application:
    check_scope(db, scope)
    return await _resource(profiles, await _require_application_v2(db, application_id))


async def update_application_v2(
    db: AsyncSession,
    scope: AccessScope,
    application_id: uuid.UUID,
    payload: v2.ApplicationUpdate,
    profiles: ResumeProfiles,
) -> v2.Application:
    """The recruiter's changes: stage, notes, category override (only the fields sent)."""
    check_scope(db, scope)
    application = await _require_application_v2(db, application_id)
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
    (deleted,) = await _delete_applications(db, [await _require_application_v2(db, application_id)])
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
    await _require_campaign(db, campaign_id)
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
    """The activity of each of these people (one grouped query); people without applications
    get an empty :class:`PersonActivity`."""
    check_scope(db, scope)
    rows = await repository.person_activity(db, person_ids)
    return {
        pid: PersonActivity(*rows[pid]) if pid in rows else PersonActivity() for pid in person_ids
    }


async def person_applications(
    db: AsyncSession, scope: AccessScope, person_id: uuid.UUID
) -> list[v2.PersonApplication]:
    """Every application of the Person, newest first, with its campaign (one query)."""
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
        for a, c in await repository.applications_of_person(db, person_id)
    ]


async def recent_strong_matches(
    db: AsyncSession, scope: AccessScope, limit: int, profiles: ResumeProfiles
) -> list[v2.RecentMatch]:
    """The newest strong-match applications across campaigns (two queries)."""
    check_scope(db, scope)
    rows = await repository.recent_applications_in_category(db, STRONG_MATCH, limit)
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
    """The most recently updated active campaigns, with their counts (three queries)."""
    check_scope(db, scope)
    campaigns = await repository.recently_updated_campaigns(
        db, v2.CampaignStatus.active.value, limit
    )
    return await _campaign_resources(db, campaigns, tallies)


async def campaign_refs(
    db: AsyncSession, scope: AccessScope, campaign_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, v2.CampaignRef]:
    """``id -> CampaignRef`` of the campaigns among ``campaign_ids`` that exist."""
    check_scope(db, scope)
    return {c.id: _campaign_ref(c) for c in await repository.campaigns_by_id(db, campaign_ids)}


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
    found = await repository.application_ids_of(db, pairs)
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
    check_scope(db, scope)
    return await repository.get_campaign(db, campaign_id) is not None


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


def legacy_candidate_map(db: Session) -> dict[uuid.UUID, uuid.UUID]:
    """Every ``legacy_candidate_map`` row, ``candidate_id -> application_id`` (the historical id
    map of merged legacy candidates, spec §7)."""
    return repository.legacy_map_rows_sync(db)
