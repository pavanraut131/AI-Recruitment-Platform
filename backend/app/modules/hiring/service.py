"""hiring service: the public interface of the hiring module.

Campaign CRUD and stats, the global stats, job description parsing, and the candidate list,
export, detail, recruiter updates and deletes. The routers in ``router.py`` only translate HTTP
to these calls; other modules (``ingestion``) and the composition layer (``app.api``) may import
this module and ``app.modules.hiring.schemas``, never ``models`` or ``repository``
(import-linter enforces it).

Business rules live here: the category / pipeline-stage vocabularies, the candidate filter
builder shared by the list and the CSV export, and the stats aggregation. Job descriptions are
parsed by the ``ai`` module. Errors are raised as :class:`HiringError` subclasses carrying the
user-facing ``detail``; the routers map them to status codes.

Cross-module boundaries (E0-10)
-------------------------------
Hiring never reads or writes another module's tables. Where a use case needs another module,
the data crosses this interface instead:

1. **Stats** (``GET /campaigns/{id}``, ``GET /campaigns/{id}/stats``, ``GET /stats``) need
   resume counts by status, which ingestion owns. :func:`campaign_stats` and
   :func:`global_stats` take them as an argument; ``app.api`` asks ingestion for them and
   composes the endpoint.
2. **Deleting candidates** (``DELETE /candidates/{id}``, ``DELETE
   /campaigns/{id}/candidates/bulk``) also deletes their resumes. :func:`delete_candidate` and
   :func:`bulk_delete_candidates` delete (flush, don't commit) and return the resume ids;
   ``app.api`` has ingestion delete those resumes and commits both in one transaction.
   Since E0-18 they also keep the candidates' applications consistent (see "Mirroring the
   legacy writes" below), which needs the resumes' upload times: ``app.api`` passes ingestion's
   lookup in as the ``uploaded_at`` callable.
5. **Recruiter updates** (``PUT /candidates/{id}/category``, ``PATCH /candidates/{id}/notes``,
   ``PATCH /candidates/{id}/pipeline``, ``PATCH /campaigns/{id}/candidates/bulk-pipeline``)
   mirror the write onto the application (E0-18) and take the same ``uploaded_at`` callable,
   so they are composed in ``app.api`` as well.
3. **Deleting a campaign** removes its resumes through the database's
   ``resumes.campaign_id ON DELETE CASCADE`` (see ``models.py``), so it stays a hiring endpoint.
4. **The upload pipeline** (``app.modules.ingestion``) checks campaigns and writes candidates
   only through the functions under "Upload pipeline" below, and applications through
   :func:`upsert_application` (dual write, E0-16) and :func:`link_legacy_candidate` (E0-17).
   The backfill and its verification (``app.modules.ingestion.backfill`` / ``verify_backfill``)
   read candidates and applications through the functions under "Legacy candidates ->
   applications"; resume upload times cross the boundary as arguments. Its scoring input is the ``ai`` module's
   ``ScoringResult`` (``app.modules.ai.schemas``, public): the pipeline passes on what the
   model returned, and hiring keeps no second copy of that shape to keep in sync.

Mirroring the legacy writes (E0-18, spec §6.1 "switch gate")
-----------------------------------------------------------
Until the read switch the API still writes ``candidates``; every such write now keeps the
candidate's application equal to what the backfill would compute from its candidates, in the
same transaction. A candidate's application is the one with the candidate's id, else the one
``legacy_candidate_map`` redirects it to; a candidate with neither (resume still processing)
is written alone.

- **Recruiter fields** (:func:`expected_recruiter_fields`): the application's
  ``pipeline_stage`` and ``notes`` are, per field, the value of the newest candidate (by resume
  upload time) whose value is not ``NULL``. Its ``category`` is the category of the candidate
  of the application's current resume (``applications.resume_id``): it travels with the
  scoring, so a manual category on an older, merged candidate is not mirrored. A write is
  "update the candidate, then recompute the application from all its candidates", never "copy
  the new value", so a merged application stays right whichever candidate is edited.
- **Deletes**: an application is deleted with its last candidate. While other candidates lead
  to it, it is kept (with its id, even when that was the deleted candidate's id), only the
  deleted candidates' map rows go, the scoring moves to the newest remaining resume if the
  deleted candidate held the current one, and the recruiter fields are recomputed.
- **Locks**: candidates are locked before their applications (``FOR UPDATE``, each in id
  order), the order the upload pipeline writes them in, and the recomputation reads the
  candidates only once it holds the application's lock, so concurrent edits of two candidates
  of one application cannot each miss the other.
"""

import csv
import io
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, TypeVar

import structlog
from sqlalchemy import ColumnElement, column, exists, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.ai import service as ai_service
from app.modules.ai.schemas import ExtractionResult, ScoringResult
from app.modules.hiring import repository
from app.modules.hiring.models import Application, Campaign, Candidate
from app.modules.hiring.schemas import (
    CampaignCreate,
    CampaignResponse,
    CampaignStats,
    CampaignUpdate,
    GlobalStats,
    JDParseRequest,
    JDParseResponse,
)
from app.platform.sql import LIKE_ESCAPE, escape_like

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
    campaign = await _require_campaign(db, campaign_id)
    await repository.delete_campaign(db, campaign)


# ── Stats ─────────────────────────────────────────────────────
async def campaign_stats(
    db: AsyncSession, campaign_id: uuid.UUID, resume_counts: Mapping[str, int]
) -> CampaignStats:
    """Stats for one campaign, given its resume counts by status (from ingestion).

    An unknown campaign id yields all-zero stats, not a 404.
    """
    category_rows = await repository.candidate_category_stats(db, campaign_id)
    skill_lists = await repository.candidate_skill_lists(db, campaign_id)
    return build_campaign_stats(resume_counts, category_rows, skill_lists)


def build_campaign_stats(
    resume_counts: Mapping[str, int],
    category_rows: Iterable[tuple[str | None, int, float | None]],
    skill_lists: Iterable[list[str] | None],
) -> CampaignStats:
    """Aggregate a campaign's resume counts by status, candidate ``(category, count, average
    score)`` rows and candidates' skill lists into :class:`CampaignStats`."""
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
    ingestion): single-table queries, no joins."""
    campaign_counts = await repository.campaign_status_counts(db)
    total_candidates = await repository.count_candidates(db)
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


def build_candidate_filters(filters: CandidateFilters) -> list[ColumnElement[bool]]:
    """The SQL ``WHERE`` clauses (to be AND-ed) for ``filters``."""
    clauses: list[ColumnElement[bool]] = [Candidate.campaign_id == filters.campaign_id]
    if filters.category:
        clauses.append(Candidate.category == filters.category)
    if filters.min_score is not None:
        clauses.append(Candidate.score >= filters.min_score)
    if filters.max_score is not None:
        clauses.append(Candidate.score <= filters.max_score)
    if filters.min_experience is not None:
        clauses.append(Candidate.years_of_experience >= filters.min_experience)
    if filters.search:
        search_pattern = f"%{escape_like(filters.search)}%"
        clauses.append(
            or_(
                Candidate.name.ilike(search_pattern, escape=LIKE_ESCAPE),
                Candidate.email.ilike(search_pattern, escape=LIKE_ESCAPE),
            )
        )
    if filters.skill:
        # Any skill contains the term, case-insensitively:
        #   EXISTS (SELECT 1 FROM jsonb_array_elements_text(candidates.skills) AS skill
        #           WHERE skill.value ILIKE :pattern ESCAPE '\')
        # Matching each element (rather than `skills::text`) keeps the JSON syntax (quotes,
        # commas, brackets) out of the match. An empty array has no elements: never matches.
        skill_elem = (
            func.jsonb_array_elements_text(Candidate.skills)
            .table_valued(column("value"))
            .alias("skill")
        )
        clauses.append(
            exists()
            .select_from(skill_elem)
            .where(skill_elem.c.value.ilike(f"%{escape_like(filters.skill)}%", escape=LIKE_ESCAPE))
        )
    if filters.pipeline_stage:
        clauses.append(Candidate.pipeline_stage == filters.pipeline_stage)
    return clauses


# ── Candidates ────────────────────────────────────────────────
async def list_candidates(
    db: AsyncSession, filters: CandidateFilters, page: int, page_size: int
) -> Sequence[Candidate]:
    """One page of matching candidates, best score first (unscored last)."""
    return await repository.list_candidates(
        db, build_candidate_filters(filters), offset=(page - 1) * page_size, limit=page_size
    )


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


async def export_candidates_csv(db: AsyncSession, filters: CandidateFilters) -> str:
    """All matching candidates (no paging) as a CSV document, best score first."""
    candidates = await repository.list_candidates(db, build_candidate_filters(filters))
    return render_candidates_csv(candidates)


def _join(values: Sequence[Any] | None) -> str:
    return " | ".join(str(v) for v in (values or []))


def render_candidates_csv(candidates: Iterable[Candidate]) -> str:
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


async def _require_candidate(
    db: AsyncSession, candidate_id: uuid.UUID, *, for_update: bool = False
) -> Candidate:
    candidate = await repository.get_candidate(db, candidate_id, for_update=for_update)
    if not candidate:
        raise NotFoundError("Candidate not found")
    return candidate


async def get_candidate(db: AsyncSession, candidate_id: uuid.UUID) -> Candidate:
    return await _require_candidate(db, candidate_id)


async def get_candidate_by_resume(db: AsyncSession, resume_id: uuid.UUID) -> Candidate:
    candidate = await repository.get_candidate_by_resume(db, resume_id)
    if not candidate:
        raise NotFoundError("Candidate not yet processed")
    return candidate


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


# The upload time of each of these resumes (ingestion's data; ``app.api`` passes ingestion's
# lookup). Resumes that do not exist are left out.
AsyncUploadTimes = Callable[[Sequence[uuid.UUID]], Awaitable[Mapping[uuid.UUID, datetime]]]


async def update_candidate_category(
    db: AsyncSession, candidate_id: uuid.UUID, category: str, uploaded_at: AsyncUploadTimes
) -> Candidate:
    """Recruiter override of the AI category. The value is checked before the lookup.

    Mirrored onto the application only when this candidate holds the application's current
    resume (the category travels with the scoring; module docstring)."""
    _check_category(category)
    candidate = await _require_candidate(db, candidate_id, for_update=True)
    candidate.category = category
    await _mirror_to_applications(db, [candidate.id], uploaded_at)
    await repository.commit_and_refresh(db, candidate)
    logger.info("category_overridden", candidate_id=str(candidate_id), category=category)
    return candidate


async def update_candidate_notes(
    db: AsyncSession, candidate_id: uuid.UUID, notes: str, uploaded_at: AsyncUploadTimes
) -> Candidate:
    candidate = await _require_candidate(db, candidate_id, for_update=True)
    candidate.notes = notes
    await _mirror_to_applications(db, [candidate.id], uploaded_at)
    await repository.commit_and_refresh(db, candidate)
    logger.info("notes_updated", candidate_id=str(candidate_id))
    return candidate


async def update_candidate_pipeline(
    db: AsyncSession, candidate_id: uuid.UUID, stage: str, uploaded_at: AsyncUploadTimes
) -> Candidate:
    """Move one candidate to a pipeline stage. The stage is checked before the lookup."""
    _check_pipeline_stage(stage)
    candidate = await _require_candidate(db, candidate_id, for_update=True)
    candidate.pipeline_stage = stage
    await _mirror_to_applications(db, [candidate.id], uploaded_at)
    await repository.commit_and_refresh(db, candidate)
    logger.info("pipeline_updated", candidate_id=str(candidate_id), stage=stage)
    return candidate


async def bulk_update_pipeline(
    db: AsyncSession,
    campaign_id: uuid.UUID,
    candidate_ids: Sequence[uuid.UUID],
    stage: str,
    uploaded_at: AsyncUploadTimes,
) -> int:
    """Move the campaign's candidates among ``candidate_ids`` to ``stage``; returns how many."""
    _check_pipeline_stage(stage)
    candidates = await repository.candidates_in_campaign(
        db, campaign_id, candidate_ids, for_update=True
    )
    for c in candidates:
        c.pipeline_stage = stage
    await _mirror_to_applications(db, [c.id for c in candidates], uploaded_at)
    await repository.commit(db)
    logger.info("bulk_pipeline_updated", count=len(candidates), stage=stage)
    return len(candidates)


async def bulk_delete_candidates(
    db: AsyncSession,
    campaign_id: uuid.UUID,
    candidate_ids: Sequence[uuid.UUID],
    uploaded_at: AsyncUploadTimes,
) -> list[uuid.UUID]:
    """Delete the campaign's candidates among ``candidate_ids`` (others are ignored), and their
    applications as the delete rule says (module docstring).

    Flushes but does not commit: the caller deletes the returned resume ids (one per deleted
    candidate) in the same transaction and commits.
    """
    candidates = await repository.candidates_in_campaign(
        db, campaign_id, candidate_ids, for_update=True
    )
    resume_ids = [c.resume_id for c in candidates]
    await _delete_candidates(db, candidates, uploaded_at)
    logger.info("bulk_candidates_deleted", count=len(candidates))
    return resume_ids


async def delete_candidate(
    db: AsyncSession, candidate_id: uuid.UUID, uploaded_at: AsyncUploadTimes
) -> uuid.UUID:
    """Delete one candidate, and its application as the delete rule says (module docstring);
    returns its resume id.

    Flushes but does not commit: the caller deletes the resume in the same transaction and
    commits.
    """
    candidate = await _require_candidate(db, candidate_id, for_update=True)
    await _delete_candidates(db, [candidate], uploaded_at)
    logger.info("candidate_deleted", candidate_id=str(candidate_id))
    return candidate.resume_id


async def _mirror_to_applications(
    db: AsyncSession, candidate_ids: Sequence[uuid.UUID], uploaded_at: AsyncUploadTimes
) -> None:
    """Recompute the mirrored fields of the applications these (already changed, locked)
    candidates lead to. Candidates without an application are left alone."""
    links = await repository.application_links(db, candidate_ids)  # autoflush: changes visible
    applications = await repository.lock_applications(db, sorted(set(links.values())))
    if not applications:
        return
    linked = _group(await repository.candidates_linked_to(db, [a.id for a in applications]))
    times = await uploaded_at([c.resume_id for cs in linked.values() for c in cs])
    for application in applications:
        changed = _apply(
            application,
            expected_recruiter_fields(
                application.resume_id,
                [_recruiter_view(c) for c in linked.get(application.id, [])],
                times,
            ),
        )
        if changed:
            logger.info(
                "application_recruiter_fields_mirrored",
                application_id=str(application.id),
                fields=changed,
            )
    await repository.flush(db)


async def _delete_candidates(
    db: AsyncSession, candidates: Sequence[Candidate], uploaded_at: AsyncUploadTimes
) -> None:
    """Delete these (locked) candidates and apply the delete rule to their applications."""
    links = await repository.application_links(db, [c.id for c in candidates])
    for c in candidates:
        await repository.delete_candidate(db, c)
    await repository.flush(db)
    applications = await repository.lock_applications(db, sorted(set(links.values())))
    if not applications:
        return

    deleted_resumes = {c.resume_id for c in candidates}
    remaining = _group(await repository.candidates_linked_to(db, [a.id for a in applications]))
    times = await uploaded_at([c.resume_id for cs in remaining.values() for c in cs])
    for application in applications:
        survivors = newest_first(remaining.get(application.id, []), times)
        if not survivors:
            await repository.delete_application(db, application)
            logger.info(
                "application_deleted_with_last_candidate", application_id=str(application.id)
            )
            continue
        # Kept: its id stays, even when it was a deleted candidate's id (its other candidates'
        # map rows point at it). Only the deleted candidates' own redirects go.
        await repository.delete_legacy_map_rows(
            db, [cid for cid, aid in links.items() if aid == application.id]
        )
        if application.resume_id in deleted_resumes:
            _take_scoring(application, survivors[0], times[survivors[0].resume_id])
        _apply(
            application,
            expected_recruiter_fields(
                application.resume_id, [_recruiter_view(c) for c in survivors], times
            ),
        )
        logger.info(
            "application_kept_after_candidate_delete",
            application_id=str(application.id),
            remaining_candidates=len(survivors),
        )
    await repository.flush(db)


def _take_scoring(application: Application, candidate: Candidate, uploaded_at: datetime) -> None:
    """The application's current resume was deleted: it takes the resume and stored scoring of
    ``candidate`` (the newest remaining one), as the backfill would over the remaining
    candidates (newer resume wins, manual category included)."""
    application.resume_id = candidate.resume_id
    application.resume_uploaded_at = uploaded_at
    application.score = candidate.score
    application.score_breakdown = candidate.score_breakdown
    application.category = candidate.category
    application.summary = candidate.summary
    application.strengths = candidate.strengths if candidate.strengths is not None else []
    application.weaknesses = candidate.weaknesses if candidate.weaknesses is not None else []
    application.missing_skills = (
        candidate.missing_skills if candidate.missing_skills is not None else []
    )
    application.recommendation = candidate.recommendation


def _group(rows: Iterable[tuple[uuid.UUID, Candidate]]) -> dict[uuid.UUID, list[Candidate]]:
    grouped: dict[uuid.UUID, list[Candidate]] = {}
    for application_id, candidate in rows:
        grouped.setdefault(application_id, []).append(candidate)
    return grouped


def _recruiter_view(candidate: Candidate) -> "RecruiterCandidate":
    return RecruiterCandidate(
        id=candidate.id,
        resume_id=candidate.resume_id,
        pipeline_stage=candidate.pipeline_stage,
        notes=candidate.notes,
        category=candidate.category,
    )


# ── Upload pipeline ───────────────────────────────────────────
# Called by app.modules.ingestion: the upload endpoint (async) and the Celery tasks (sync). The
# tasks run in worker processes without an event loop and use a synchronous SQLAlchemy Session,
# so these take that Session and never commit: the task changes the resume's status and the
# candidate in one transaction and commits it, as before the module split.
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


def save_resume_text(
    db: Session, *, resume_id: uuid.UUID, campaign_id: uuid.UUID, raw_text: str
) -> None:
    """Store a resume's extracted text on its candidate, creating the (not yet analysed)
    candidate on first extraction."""
    candidate = repository.get_candidate_by_resume_sync(db, resume_id)
    if not candidate:
        repository.add_sync(
            db, Candidate(resume_id=resume_id, campaign_id=campaign_id, raw_text=raw_text)
        )
    else:
        candidate.raw_text = raw_text


def get_resume_text(db: Session, resume_id: uuid.UUID) -> str | None:
    """The extracted text stored for a resume's candidate (``None``: no candidate or no text)."""
    candidate = repository.get_candidate_by_resume_sync(db, resume_id)
    return candidate.raw_text if candidate else None


def _require_candidate_for_resume(db: Session, resume_id: uuid.UUID) -> Candidate:
    candidate = repository.get_candidate_by_resume_sync(db, resume_id)
    if not candidate:
        raise NotFoundError("Candidate not found")
    return candidate


def record_extraction(db: Session, resume_id: uuid.UUID, profile: ExtractionResult) -> None:
    """Copy the AI-extracted profile onto the resume's candidate."""
    candidate = _require_candidate_for_resume(db, resume_id)
    candidate.name = profile.name
    candidate.email = profile.email
    candidate.phone = profile.phone
    candidate.github_url = profile.github_url
    candidate.linkedin_url = profile.linkedin_url
    candidate.skills = profile.skills
    candidate.education = profile.education
    candidate.work_experience = profile.work_experience
    candidate.certifications = profile.certifications
    candidate.projects = profile.projects
    candidate.years_of_experience = profile.years_of_experience


def record_scoring(db: Session, resume_id: uuid.UUID, scoring: ScoringResult) -> None:
    """Copy the AI score and assessment onto the resume's candidate."""
    candidate = _require_candidate_for_resume(db, resume_id)
    candidate.score = scoring.score
    candidate.score_breakdown = scoring.score_breakdown
    candidate.category = scoring.category
    candidate.summary = scoring.summary
    candidate.strengths = scoring.strengths
    candidate.weaknesses = scoring.weaknesses
    candidate.missing_skills = scoring.missing_skills
    candidate.recommendation = scoring.recommendation


# ── Applications (Person/Application split, dual write) ───────
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

    ``application_id`` is the id for a **new** row (the backfill passes the legacy candidate's
    id, spec §6.2); it is ignored when the application already exists. Default: a new UUID.

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


# ── Legacy candidates -> applications (E0-17) ─────────────────
# Every legacy candidate must lead to exactly one application (spec §6.2–6.3): the application
# reuses the candidate's id, or ``legacy_candidate_map`` redirects the candidate's id to the
# application it was merged into. Both the upload pipeline (from E0-17 on) and the backfill
# (``app.modules.ingestion.backfill``) keep that link with the functions below.
RECRUITER_FIELDS = ("pipeline_stage", "notes")


@dataclass(frozen=True)
class LegacyCandidate:
    """One ``candidates`` row as the backfill reads it (spec §6.2)."""

    id: uuid.UUID
    campaign_id: uuid.UUID
    resume_id: uuid.UUID
    created_at: datetime
    name: str | None
    email: str | None
    phone: str | None
    github_url: str | None
    linkedin_url: str | None
    skills: list[Any] | None
    education: list[Any] | None
    work_experience: list[Any] | None
    certifications: list[Any] | None
    projects: list[Any] | None
    years_of_experience: float | None
    score: float | None
    score_breakdown: dict[str, Any] | None
    category: str | None
    summary: str | None
    strengths: list[Any] | None
    weaknesses: list[Any] | None
    missing_skills: list[Any] | None
    recommendation: str | None
    pipeline_stage: str | None
    notes: str | None

    @property
    def position(self) -> tuple[datetime, uuid.UUID]:
        """The keyset position of this candidate: ``(created_at, id)``."""
        return (self.created_at, self.id)

    def extraction(self) -> ExtractionResult:
        """The AI extraction this candidate was written from (inverse of
        :func:`record_extraction`), the shape the pipeline stores in ``resumes.extracted``."""
        return ExtractionResult(
            name=self.name,
            email=self.email,
            phone=self.phone,
            github_url=self.github_url,
            linkedin_url=self.linkedin_url,
            skills=self.skills or [],
            education=self.education or [],
            work_experience=self.work_experience or [],
            certifications=self.certifications or [],
            projects=self.projects or [],
            years_of_experience=self.years_of_experience,
        )

    def scoring(self) -> ScoringResult:
        """The scoring stored on this candidate (inverse of :func:`record_scoring`), including a
        category the recruiter changed by hand.

        Copied **as stored**, without validation: a legacy row may hold ``NULL`` where
        ``ScoringResult`` wants a value (e.g. ``summary``), and the application must mirror the
        candidate exactly so the compatibility layer (spec §7) can rebuild the same response.
        """
        stored: dict[str, Any] = {
            "score": self.score,
            "score_breakdown": self.score_breakdown,
            "category": self.category,
            "summary": self.summary,
            "strengths": self.strengths if self.strengths is not None else [],
            "weaknesses": self.weaknesses if self.weaknesses is not None else [],
            "missing_skills": self.missing_skills if self.missing_skills is not None else [],
            "recommendation": self.recommendation,
        }
        return ScoringResult.model_construct(**stored)


@dataclass(frozen=True)
class LegacyCandidateRef:
    id: uuid.UUID
    campaign_id: uuid.UUID
    resume_id: uuid.UUID


@dataclass(frozen=True)
class ApplicationRef:
    id: uuid.UUID
    campaign_id: uuid.UUID
    person_id: uuid.UUID
    resume_id: uuid.UUID


def iter_legacy_candidates_after(
    db: Session, after: tuple[datetime, uuid.UUID] | None, limit: int
) -> list[LegacyCandidate]:
    """The next page of legacy candidates in ``(created_at, id)`` order, strictly after the
    keyset position ``after`` (``None``: from the start). Never ``OFFSET`` (spec §6.2)."""
    rows = repository.legacy_candidates_after_sync(db, after, limit)
    return [LegacyCandidate(**row._asdict()) for row in rows]


def legacy_candidate_id_for_resume(db: Session, resume_id: uuid.UUID) -> uuid.UUID | None:
    return repository.get_candidate_id_by_resume_sync(db, resume_id)


def legacy_links(db: Session, candidate_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    """The application each of these candidates already leads to (its own id, or the
    ``legacy_candidate_map`` redirect); candidates with neither are left out."""
    links = repository.legacy_map_rows_sync(db, candidate_ids)
    for application_id in repository.existing_application_ids_sync(db, candidate_ids):
        links.setdefault(application_id, application_id)
    return links


def link_legacy_candidate(
    db: Session, *, candidate_id: uuid.UUID, application_id: uuid.UUID
) -> bool:
    """Make ``candidate_id`` lead to ``application_id``: nothing to do when the application
    reuses the candidate's id, else a ``legacy_candidate_map`` row (``ON CONFLICT DO NOTHING``,
    so it is idempotent). Returns whether a map row was inserted. Never commits."""
    if candidate_id == application_id:
        return False
    inserted = repository.insert_legacy_map_row_sync(db, candidate_id, application_id)
    if inserted:
        logger.info(
            "legacy_candidate_mapped",
            candidate_id=str(candidate_id),
            application_id=str(application_id),
        )
    return inserted


# ── Recruiter fields: one rule for every write path (E0-18) ──
# The rule (module docstring, "Mirroring the legacy writes") is computed here only; the API's
# mirrored writes and deletes, the upload pipeline, the backfill, ``backfill
# --resync-recruiter-fields`` and ``verify_backfill`` all call it. Hiring does not read
# ``resumes``: the callers pass the resumes' upload times in.
UploadTimes = Callable[[Sequence[uuid.UUID]], Mapping[uuid.UUID, datetime]]


@dataclass(frozen=True)
class RecruiterCandidate:
    """What the recruiter-field rule reads of one legacy candidate."""

    id: uuid.UUID
    resume_id: uuid.UUID
    pipeline_stage: str | None
    notes: str | None
    category: str | None


class _HasResume(Protocol):
    @property
    def resume_id(self) -> uuid.UUID: ...


_R = TypeVar("_R", bound=_HasResume)


def newest_first(candidates: Iterable[_R], uploaded_at: Mapping[uuid.UUID, datetime]) -> list[_R]:
    """``candidates`` by their resume's upload time, newest first (ties: the larger resume id
    first, the order ``upsert_application`` uses). Candidates whose resume time is unknown are
    left out."""
    return sorted(
        (c for c in candidates if c.resume_id in uploaded_at),
        key=lambda c: (uploaded_at[c.resume_id], c.resume_id),
        reverse=True,
    )


def expected_recruiter_fields(
    application_resume_id: uuid.UUID,
    candidates: Iterable[RecruiterCandidate],
    uploaded_at: Mapping[uuid.UUID, datetime],
) -> dict[str, str | None]:
    """The mirrored fields an application must hold, given every candidate that leads to it.

    - ``pipeline_stage``, ``notes``: per field, the value of the newest candidate (by resume
      upload time) whose value is not ``NULL``; ``NULL`` if none has one. A candidate without a
      value never clears another's; an empty note is a value (the recruiter cleared it).
    - ``category``: the category of the candidate whose resume is the application's current
      one (``application_resume_id``); left out when no such candidate exists.

    Depends only on the set of candidates, never on the order they were written in. Returns
    ``{}`` (nothing to say) when no candidate with a known upload time leads to it.
    """
    ordered = newest_first(candidates, uploaded_at)
    if not ordered:
        return {}
    expected: dict[str, str | None] = {
        name: next(
            (value for c in ordered if (value := getattr(c, name)) is not None),
            None,
        )
        for name in RECRUITER_FIELDS
    }
    current = next((c for c in ordered if c.resume_id == application_resume_id), None)
    if current is not None:
        expected["category"] = current.category
    return expected


def _apply(application: Application, expected: Mapping[str, str | None]) -> list[str]:
    """Set the fields of ``application`` that differ from ``expected``; returns their names."""
    changed = [name for name, value in expected.items() if getattr(application, name) != value]
    for name in changed:
        setattr(application, name, expected[name])
    return changed


def _linked_recruiter_candidates(
    db: Session, application_ids: Sequence[uuid.UUID] | None
) -> dict[uuid.UUID, list[RecruiterCandidate]]:
    grouped: dict[uuid.UUID, list[RecruiterCandidate]] = {}
    for row in repository.linked_candidates_sync(db, application_ids):
        grouped.setdefault(row.application_id, []).append(
            RecruiterCandidate(
                id=row.id,
                resume_id=row.resume_id,
                pipeline_stage=row.pipeline_stage,
                notes=row.notes,
                category=row.category,
            )
        )
    return grouped


def carry_recruiter_fields(
    db: Session, application_ids: Sequence[uuid.UUID], uploaded_at: UploadTimes
) -> dict[uuid.UUID, list[str]]:
    """Recompute the mirrored fields (``pipeline_stage``, ``notes``, ``category``) of these
    applications from the legacy candidates that lead to them (:func:`expected_recruiter_fields`).
    Never commits.

    Used by the backfill, the upload pipeline (after it links a candidate) and ``backfill
    --resync-recruiter-fields``. Locks the applications (``FOR UPDATE``, id order) before it
    reads their candidates, so a concurrent recruiter edit is either seen or waits for this
    transaction and recomputes after it. Idempotent. ``uploaded_at`` returns the upload time
    of each resume it is given.

    Returns the changed fields per application (only the changed applications).
    """
    applications = repository.lock_applications_sync(db, sorted(set(application_ids)))
    if not applications:
        return {}
    linked = _linked_recruiter_candidates(db, [a.id for a in applications])
    times = uploaded_at([c.resume_id for cs in linked.values() for c in cs])
    changed: dict[uuid.UUID, list[str]] = {}
    for application in applications:
        fields = _apply(
            application,
            expected_recruiter_fields(application.resume_id, linked.get(application.id, []), times),
        )
        if fields:
            changed[application.id] = fields
    if changed:
        db.flush()
    return changed


def application_ids_after(db: Session, after: uuid.UUID | None, limit: int) -> list[uuid.UUID]:
    """The next page of application ids in id order, strictly after ``after`` (keyset)."""
    return repository.application_ids_after_sync(db, after, limit)


def recruiter_field_mismatches(db: Session, uploaded_at: UploadTimes) -> list[str]:
    """Every application whose mirrored fields differ from :func:`expected_recruiter_fields`
    over its candidates, one message per field (verification, E0-18). Read-only."""
    linked = _linked_recruiter_candidates(db, None)
    times = uploaded_at([c.resume_id for cs in linked.values() for c in cs])
    problems: list[str] = []
    for app in repository.application_recruiter_states_sync(db):
        expected = expected_recruiter_fields(app.resume_id, linked.get(app.id, []), times)
        for name, value in expected.items():
            if getattr(app, name) != value:
                problems.append(
                    f"application {app.id}: {name} is {getattr(app, name)!r}, "
                    f"its candidates give {value!r}"
                )
    return problems


def legacy_candidate_refs(db: Session) -> list[LegacyCandidateRef]:
    """Every legacy candidate (verification, spec §6.3)."""
    return [LegacyCandidateRef(**row._asdict()) for row in repository.candidate_refs_sync(db)]


def application_refs(db: Session) -> list[ApplicationRef]:
    """Every application (verification, spec §6.3)."""
    return [ApplicationRef(**row._asdict()) for row in repository.application_refs_sync(db)]


def legacy_candidate_map(db: Session) -> dict[uuid.UUID, uuid.UUID]:
    """Every ``legacy_candidate_map`` row, ``candidate_id -> application_id``."""
    return repository.legacy_map_rows_sync(db)
