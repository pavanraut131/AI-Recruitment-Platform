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
"""

import csv
import io
import uuid
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import structlog
from sqlalchemy import ColumnElement, any_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.ai import service as ai_service
from app.modules.ai.schemas import ScoringResult
from app.modules.hiring import repository
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
from app.platform.sql import uuid_array

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


# ── Campaigns ─────────────────────────────────────────────────
async def create_campaign(db: AsyncSession, payload: CampaignCreate) -> Campaign:
    campaign = await repository.add_campaign(db, Campaign(**payload.model_dump()))
    logger.info("campaign_created", id=str(campaign.id), title=campaign.title)
    return campaign


async def list_campaigns(db: AsyncSession, skip: int, limit: int) -> Sequence[Campaign]:
    return await repository.list_campaigns(db, skip, limit)


async def _require_campaign(db: AsyncSession, campaign_id: uuid.UUID) -> Campaign:
    campaign = await repository.get_campaign(db, campaign_id)
    if not campaign:
        raise NotFoundError("Campaign not found")
    return campaign


async def get_campaign(db: AsyncSession, campaign_id: uuid.UUID) -> CampaignResponse:
    """The campaign, without stats (``stats`` is ``None``; see :func:`campaign_stats`)."""
    campaign = await _require_campaign(db, campaign_id)
    return CampaignResponse.model_validate(campaign)


async def update_campaign(
    db: AsyncSession, campaign_id: uuid.UUID, payload: CampaignUpdate
) -> Campaign:
    """Apply the fields the client sent (unset fields are left alone)."""
    campaign = await _require_campaign(db, campaign_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(campaign, field, value)
    await repository.commit_and_refresh(db, campaign)
    return campaign


async def delete_campaign(db: AsyncSession, campaign_id: uuid.UUID) -> None:
    """Delete the campaign and its applications (``applications.campaign_id ON DELETE
    CASCADE``; their ``legacy_candidate_map`` rows go with them).

    Flushes but does not commit: the caller (``app.api``) has ingestion delete the campaign's
    resumes in the same transaction and commits. Raises :class:`NotFoundError` for an unknown
    campaign.
    """
    campaign = await _require_campaign(db, campaign_id)
    await repository.delete_campaign(db, campaign)
    logger.info("campaign_deleted", id=str(campaign_id))


# ── Stats ─────────────────────────────────────────────────────
async def campaign_stats(
    db: AsyncSession,
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


async def global_stats(db: AsyncSession, resume_counts: Mapping[str, int]) -> GlobalStats:
    """Aggregate counts across all campaigns, given the resume counts by status (from
    ingestion): single-table queries, no joins. ``total_candidates`` counts applications."""
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
    filters: CandidateFilters,
    page: int,
    page_size: int,
    profiles: ResumeProfiles,
) -> list[CandidateListItem]:
    """One page of matching candidates (applications), best score first (unscored last)."""
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
    db: AsyncSession, filters: CandidateFilters, profiles: ResumeProfiles
) -> str:
    """All matching candidates (no paging) as a CSV document, best score first."""
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
    db: AsyncSession, candidate_id: uuid.UUID, profiles: ResumeProfiles
) -> CandidateDetail:
    """The candidate (application) with this id, or the application a merged legacy candidate
    with this id was redirected to; the response carries the application's id."""
    return await _detail(profiles, await _require_application(db, candidate_id))


async def get_candidate_by_resume(
    db: AsyncSession, resume_id: uuid.UUID, profiles: ResumeProfiles
) -> CandidateDetail:
    """The application a resume belongs to: the one it is the current resume of, else (an
    older resume of a merged application) the application of its Person in its campaign.
    404 while the resume is not processed yet (no application)."""
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
    db: AsyncSession, candidate_id: uuid.UUID, category: str, profiles: ResumeProfiles
) -> CandidateDetail:
    """Recruiter override of the AI category. The value is checked before the lookup."""
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
    db: AsyncSession, candidate_id: uuid.UUID, notes: str, profiles: ResumeProfiles
) -> CandidateDetail:
    application = await _require_application(db, candidate_id)
    application.notes = notes
    await repository.commit_and_refresh(db, application)
    logger.info("notes_updated", candidate_id=str(candidate_id), application_id=str(application.id))
    return await _detail(profiles, application)


async def update_candidate_pipeline(
    db: AsyncSession, candidate_id: uuid.UUID, stage: str, profiles: ResumeProfiles
) -> CandidateDetail:
    """Move one candidate to a pipeline stage. The stage is checked before the lookup."""
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
    db: AsyncSession, campaign_id: uuid.UUID, candidate_ids: Sequence[uuid.UUID], stage: str
) -> int:
    """Move the campaign's candidates (applications) that ``candidate_ids`` name to ``stage``;
    returns how many applications."""
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
    db: AsyncSession, campaign_id: uuid.UUID, candidate_ids: Sequence[uuid.UUID]
) -> list[DeletedApplication]:
    """Delete the campaign's candidates (applications) that ``candidate_ids`` name (others are
    ignored).

    Flushes but does not commit: the caller deletes their resumes in the same transaction and
    commits.
    """
    applications = await repository.applications_in_campaign(
        db, campaign_id, candidate_ids, for_update=True
    )
    deleted = await _delete_applications(db, applications)
    logger.info("bulk_candidates_deleted", count=len(deleted))
    return deleted


async def delete_candidate(db: AsyncSession, candidate_id: uuid.UUID) -> DeletedApplication:
    """Delete one candidate (application). Flushes but does not commit: the caller deletes its
    resumes in the same transaction and commits."""
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


# ── Upload pipeline ───────────────────────────────────────────
# Called by app.modules.ingestion: the upload endpoint (async) and the Celery tasks (sync). The
# tasks run in worker processes without an event loop and use a synchronous SQLAlchemy Session,
# so these take that Session and never commit: the task writes the resume and the application
# in one transaction and commits it.
async def campaign_exists(db: AsyncSession, campaign_id: uuid.UUID) -> bool:
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


def application_refs(db: Session) -> list[ApplicationRef]:
    """Every application."""
    return [ApplicationRef(**row._asdict()) for row in repository.application_refs_sync(db)]


def legacy_candidate_map(db: Session) -> dict[uuid.UUID, uuid.UUID]:
    """Every ``legacy_candidate_map`` row, ``candidate_id -> application_id`` (the historical id
    map of merged legacy candidates, spec §7)."""
    return repository.legacy_map_rows_sync(db)
