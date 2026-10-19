"""The AI task's dual write (E0-16, spec §5.1, §5.5, §6.1 phase 1), with ``AI_PROVIDER=fake``.

Every processed resume writes the legacy candidate **and** a Person (``talent.resolve_person``)
and an Application (``hiring.upsert_application``), committed together. Covered here:

- matching across campaigns and within one campaign (newer resume's scoring wins), resumes
  without contact details, and an email/phone conflict;
- a failure at **each** step, retried (Celery retry) or resumed after the retries ran out
  (what the retry-failed endpoint queues), ends in exactly the state of a clean run: no
  duplicate Person, identifier, conflict or Application;
- a quota error: ``quota_exceeded`` at once, the SSE event, and a retry after Google's delay.

Fixtures (``sync_db``, ``events``, eager retries): ``tests/ingestion/conftest.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import fitz
import pytest
from celery.signals import task_retry
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.ai.fake import FakeAIService
from app.modules.ai.schemas import ExtractionResult, JobRequirements, ScoringResult
from app.modules.ai.service import AIQuotaExceeded
from app.modules.hiring import service as hiring_service
from app.modules.hiring.models import Application, Campaign, Candidate
from app.modules.ingestion import tasks
from app.modules.ingestion.models import Resume
from app.modules.talent import service as talent_service
from app.modules.talent.models import IdentityConflict, Person, PersonIdentifier, PersonResume
from tests.fakes import InMemoryStorage
from tests.ingestion.conftest import Events

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
REQUIRED_SKILLS = ["Python", "Go"]

ASHA_EMAIL = "asha.rao@example.com"
ASHA_PHONE = "+91 98765 43210"
RAVI_EMAIL = "ravi.kumar@example.com"
RAVI_PHONE = "+91 91234 56789"


def resume_text(
    name: str = "Asha Rao",
    *,
    email: str | None = ASHA_EMAIL,
    phone: str | None = ASHA_PHONE,
    skills: str = "Python, FastAPI, PostgreSQL",
    years: int = 6,
) -> str:
    contact = " | ".join(v for v in (email, phone) if v)
    lines = [
        name,
        contact,
        f"Senior Backend Engineer at Acme, {years} years of experience",
        f"Skills: {skills}",
        "B.Tech in Computer Science",
        "Project: Resume parser built with Python and Redis",
    ]
    return "\n".join(line for line in lines if line) + "\n"


# ── Arrange ───────────────────────────────────────────────────
def make_campaign(db: Session, title: str = "Backend") -> Campaign:
    campaign = Campaign(title=title, role="Backend Engineer", required_skills=REQUIRED_SKILLS)
    db.add(campaign)
    db.commit()
    return campaign


def make_resume(
    db: Session, campaign: Campaign, text: str, *, uploaded_at: datetime = T0
) -> Resume:
    """A resume as the extract task leaves it: ``processing``, its text on a placeholder
    candidate."""
    resume = Resume(
        id=uuid.uuid4(),
        campaign_id=campaign.id,
        original_filename="cv.pdf",
        storage_path=f"campaigns/{campaign.id}/cv.pdf",
        file_size_bytes=len(text),
        status="processing",
        processing_step=tasks.STEP_EXTRACT_TEXT,
        uploaded_at=uploaded_at,
    )
    db.add(resume)
    db.flush()
    hiring_service.save_resume_text(db, resume_id=resume.id, campaign_id=campaign.id, raw_text=text)
    db.commit()
    return resume


def make_pdf_resume(db: Session, storage: InMemoryStorage, campaign: Campaign, text: str) -> Resume:
    """A freshly uploaded resume (``pending``) whose PDF is in object storage."""
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text, fontsize=11)
    pdf_bytes: bytes = doc.tobytes()
    doc.close()
    resume = Resume(
        id=uuid.uuid4(),
        campaign_id=campaign.id,
        original_filename="cv.pdf",
        storage_path=f"campaigns/{campaign.id}/{uuid.uuid4()}/cv.pdf",
        file_size_bytes=len(pdf_bytes),
        status="pending",
        uploaded_at=T0,
    )
    db.add(resume)
    db.commit()
    storage.upload_file(pdf_bytes, resume.storage_path)
    return resume


def analyse(resume: Resume) -> None:
    result = tasks.process_candidate_ai.apply(args=(str(resume.id),))
    assert result.successful(), result.result


# ── Read back ─────────────────────────────────────────────────
COUNTED = {
    "people": Person,
    "person_identifiers": PersonIdentifier,
    "identity_conflicts": IdentityConflict,
    "person_resumes": PersonResume,
    "applications": Application,
    "candidates": Candidate,
}


def counts(db: Session) -> dict[str, int]:
    db.expire_all()
    return {
        name: db.execute(select(func.count()).select_from(model)).scalar_one()
        for name, model in COUNTED.items()
    }


def reload(db: Session, resume: Resume) -> Resume:
    db.expire_all()
    return db.get_one(Resume, resume.id)


def application_of(db: Session, campaign: Campaign, person_id: uuid.UUID | None) -> Application:
    return db.execute(
        select(Application).where(
            Application.campaign_id == campaign.id, Application.person_id == person_id
        )
    ).scalar_one()


def candidate_of(db: Session, resume: Resume) -> Candidate:
    return db.execute(select(Candidate).where(Candidate.resume_id == resume.id)).scalar_one()


def done_events(events: Events) -> list[dict[str, Any]]:
    return [e for e in events() if e["status"] == "done"]


# ── Matching ──────────────────────────────────────────────────
def test_repeat_applicant_to_two_campaigns_is_one_person_with_two_applications(
    sync_db: Session, fake_ai: FakeAIService, events: Events
) -> None:
    backend, platform = make_campaign(sync_db, "Backend"), make_campaign(sync_db, "Platform")
    first = make_resume(sync_db, backend, resume_text())
    second = make_resume(sync_db, platform, resume_text(), uploaded_at=T0 + timedelta(days=3))

    analyse(first)
    analyse(second)

    assert counts(sync_db) == {
        "people": 1,
        "person_identifiers": 2,
        "identity_conflicts": 0,
        "person_resumes": 2,
        "applications": 2,
        "candidates": 2,
    }
    first, second = reload(sync_db, first), reload(sync_db, second)
    assert first.person_id is not None and first.person_id == second.person_id
    for campaign, resume in ((backend, first), (platform, second)):
        application = application_of(sync_db, campaign, first.person_id)
        assert application.resume_id == resume.id
        assert application.resume_uploaded_at == resume.uploaded_at
        candidate = candidate_of(sync_db, resume)  # the legacy row, written as before
        assert (application.score, application.category) == (candidate.score, candidate.category)
    person_id = str(first.person_id)
    assert [(e["person_id"], e["matched_existing"]) for e in done_events(events)] == [
        (person_id, False),
        (person_id, True),
    ]


@pytest.mark.parametrize("newer_first", [False, True], ids=["older_first", "newer_first"])
def test_same_campaign_twice_keeps_one_application_with_the_newer_resumes_scoring(
    sync_db: Session, fake_ai: FakeAIService, newer_first: bool
) -> None:
    campaign = make_campaign(sync_db)
    older = make_resume(sync_db, campaign, resume_text(skills="Python", years=2))
    newer = make_resume(
        sync_db,
        campaign,
        resume_text(skills="Python, Go, Docker", years=8),
        uploaded_at=T0 + timedelta(days=30),
    )

    for resume in (newer, older) if newer_first else (older, newer):
        analyse(resume)

    assert counts(sync_db)["applications"] == 1
    assert counts(sync_db)["candidates"] == 2  # legacy: one candidate per resume, as before
    assert counts(sync_db)["people"] == 1
    older, newer = reload(sync_db, older), reload(sync_db, newer)
    application = application_of(sync_db, campaign, newer.person_id)
    newer_candidate, older_candidate = candidate_of(sync_db, newer), candidate_of(sync_db, older)
    assert newer_candidate.score != older_candidate.score  # the test means something
    assert application.resume_id == newer.id
    assert application.resume_uploaded_at == newer.uploaded_at
    assert (application.score, application.missing_skills, application.summary) == (
        newer_candidate.score,
        newer_candidate.missing_skills,
        newer_candidate.summary,
    )
    # Each legacy candidate keeps its own resume's scoring.
    assert older_candidate.missing_skills == ["Go"]


def test_resumes_without_contact_details_each_get_their_own_person(
    sync_db: Session, fake_ai: FakeAIService, events: Events
) -> None:
    campaign = make_campaign(sync_db)
    resumes = [
        make_resume(sync_db, campaign, resume_text(email=None, phone=None)),
        make_resume(sync_db, campaign, resume_text(email=None, phone=None)),
    ]

    for resume in resumes:
        analyse(resume)

    assert counts(sync_db) == {
        "people": 2,
        "person_identifiers": 0,
        "identity_conflicts": 0,
        "person_resumes": 2,
        "applications": 2,
        "candidates": 2,
    }
    person_ids = {reload(sync_db, r).person_id for r in resumes}
    assert len(person_ids) == 2 and None not in person_ids
    assert [e["matched_existing"] for e in done_events(events)] == [False, False]


def test_email_and_phone_of_different_people_records_a_conflict_and_uses_the_email_match(
    sync_db: Session, fake_ai: FakeAIService
) -> None:
    campaign, other = make_campaign(sync_db), make_campaign(sync_db, "Other")
    asha = make_resume(
        sync_db, campaign, resume_text("Asha Rao", email=ASHA_EMAIL, phone=ASHA_PHONE)
    )
    ravi = make_resume(
        sync_db, campaign, resume_text("Ravi Kumar", email=RAVI_EMAIL, phone=RAVI_PHONE)
    )
    mixed = make_resume(
        sync_db,
        other,
        resume_text("Asha Rao", email=ASHA_EMAIL, phone=RAVI_PHONE),
        uploaded_at=T0 + timedelta(days=1),
    )

    for resume in (asha, ravi, mixed):
        analyse(resume)

    assert counts(sync_db) == {
        "people": 2,
        "person_identifiers": 4,
        "identity_conflicts": 1,
        "person_resumes": 3,
        "applications": 3,
        "candidates": 3,
    }
    asha, ravi, mixed = (reload(sync_db, r) for r in (asha, ravi, mixed))
    assert mixed.person_id == asha.person_id != ravi.person_id
    conflict = sync_db.execute(select(IdentityConflict)).scalar_one()
    assert (conflict.resume_id, conflict.chosen_person_id, conflict.conflicting_person_id) == (
        mixed.id,
        asha.person_id,
        ravi.person_id,
    )
    assert application_of(sync_db, other, asha.person_id).resume_id == mixed.id


# ── Failure at each step, then retry ──────────────────────────
class FlakyAI(FakeAIService):
    """The fake AI, failing its next ``failures[method]`` calls of a method; counts calls."""

    def __init__(self) -> None:
        self.failures: dict[str, int] = {}
        self.error: Callable[[], Exception] = lambda: RuntimeError("AI call failed")
        self.calls: dict[str, int] = {"extract_profile": 0, "score": 0}

    def _maybe_fail(self, method: str) -> None:
        self.calls[method] += 1
        if self.failures.get(method, 0) > 0:
            self.failures[method] -= 1
            raise self.error()

    def extract_profile(self, text: str) -> ExtractionResult:
        self._maybe_fail("extract_profile")
        return super().extract_profile(text)

    def score(self, profile: ExtractionResult, requirements: JobRequirements) -> ScoringResult:
        self._maybe_fail("score")
        return super().score(profile, requirements)


class Failures:
    """Injects failures at one pipeline step. ``times`` is how many runs fail there (``FOREVER``
    outlasts the Celery retries)."""

    FOREVER = 100

    def __init__(self, monkeypatch: pytest.MonkeyPatch, ai: FlakyAI) -> None:
        self.ai = ai
        self.remaining: dict[str, int] = {}
        monkeypatch.setattr(tasks, "get_ai_service", lambda: ai)

        real_download = tasks.storage.download_file
        real_resolve = talent_service.resolve_person
        real_upsert = hiring_service.upsert_application
        real_publish = tasks.publish_resume_event

        def download(*args: Any, **kwargs: Any) -> bytes:
            self._fail("extract_text")
            return real_download(*args, **kwargs)

        def resolve(*args: Any, **kwargs: Any) -> Any:
            result = real_resolve(*args, **kwargs)  # its writes must be rolled back
            self._fail("resolve_person")
            return result

        def upsert(*args: Any, **kwargs: Any) -> Any:
            result = real_upsert(*args, **kwargs)  # its write must be rolled back
            self._fail("save")
            return result

        def publish(resume_id: str, status: str, extra: Any = None) -> None:
            real_publish(resume_id, status, extra)
            if status == "done":  # after the commit
                self._fail("after_commit")

        monkeypatch.setattr(tasks.storage, "download_file", download)
        monkeypatch.setattr(talent_service, "resolve_person", resolve)
        monkeypatch.setattr(hiring_service, "upsert_application", upsert)
        monkeypatch.setattr(tasks, "publish_resume_event", publish)

    def at(self, step: str, times: int = 1) -> None:
        if step in ("extract_profile", "score"):
            self.ai.failures[step] = times
        else:
            self.remaining[step] = times

    def clear(self) -> None:
        self.remaining.clear()
        self.ai.failures.clear()

    def _fail(self, step: str) -> None:
        if self.remaining.get(step, 0) > 0:
            self.remaining[step] -= 1
            raise RuntimeError(f"injected failure at {step}")


@pytest.fixture
def flaky_ai() -> FlakyAI:
    return FlakyAI()


@pytest.fixture
def failures(monkeypatch: pytest.MonkeyPatch, flaky_ai: FlakyAI) -> Failures:
    return Failures(monkeypatch, flaky_ai)


@pytest.fixture
def pdf_resume(sync_db: Session, fake_storage: InMemoryStorage) -> Iterator[Resume]:
    campaign = make_campaign(sync_db)
    yield make_pdf_resume(sync_db, fake_storage, campaign, resume_text())


def run_pipeline(resume: Resume) -> None:
    """Both tasks, as the worker would: text extraction, then (queued by it) AI analysis."""
    assert tasks.extract_resume.apply(args=(str(resume.id),)).successful()
    tasks.process_candidate_ai.apply(args=(str(resume.id),))


CLEAN_COUNTS = {
    "people": 1,
    "person_identifiers": 2,
    "identity_conflicts": 0,
    "person_resumes": 1,
    "applications": 1,
    "candidates": 1,
}


def final_state(db: Session, resume: Resume) -> dict[str, Any]:
    """Everything a run writes, without generated ids and timestamps."""
    resume = reload(db, resume)
    candidate = candidate_of(db, resume)
    application = application_of(db, db.get_one(Campaign, resume.campaign_id), resume.person_id)
    person = db.get_one(Person, resume.person_id)
    identifiers = db.execute(
        select(PersonIdentifier.kind, PersonIdentifier.value_normalized).where(
            PersonIdentifier.person_id == person.id
        )
    ).all()
    return {
        "counts": counts(db),
        "resume": (resume.status, resume.processing_step, resume.error_message),
        "extracted": resume.extracted,
        "candidate": (candidate.name, candidate.email, candidate.score, candidate.category),
        "application": (
            application.resume_id == resume.id,
            application.score,
            application.category,
            application.summary,
        ),
        "person": (person.full_name, person.latest_resume_id == resume.id, sorted(identifiers)),
    }


def expected_state(db: Session, resume: Resume) -> dict[str, Any]:
    """What a clean run writes, from the fake AI's answers for the resume's stored text."""
    ai = FakeAIService()
    extraction = ai.extract_profile(candidate_of(db, resume).raw_text or "")
    scoring = ai.score(
        extraction, JobRequirements(role="Backend Engineer", required_skills=REQUIRED_SKILLS)
    )
    return {
        "counts": CLEAN_COUNTS,
        "resume": ("done", None, None),
        "extracted": extraction.model_dump(mode="json"),
        "candidate": ("Asha Rao", ASHA_EMAIL, scoring.score, scoring.category),
        "application": (True, scoring.score, scoring.category, scoring.summary),
        "person": ("Asha Rao", True, [("email", ASHA_EMAIL), ("phone", "+919876543210")]),
    }


def test_a_clean_run_writes_the_expected_state(
    sync_db: Session, failures: Failures, pdf_resume: Resume
) -> None:
    run_pipeline(pdf_resume)

    assert final_state(sync_db, pdf_resume) == expected_state(sync_db, pdf_resume)
    assert failures.ai.calls == {"extract_profile": 1, "score": 1}


@pytest.mark.parametrize(
    ("step", "ai_calls"),
    [
        ("extract_text", {"extract_profile": 1, "score": 1}),
        ("extract_profile", {"extract_profile": 2, "score": 1}),
        # From here on the extraction snapshot (resumes.extracted) is reused: no second call.
        ("score", {"extract_profile": 1, "score": 2}),
        ("resolve_person", {"extract_profile": 1, "score": 2}),
        ("save", {"extract_profile": 1, "score": 2}),
        ("after_commit", {"extract_profile": 1, "score": 2}),
    ],
)
def test_a_failure_at_any_step_is_retried_without_duplicates(
    sync_db: Session,
    failures: Failures,
    pdf_resume: Resume,
    step: str,
    ai_calls: dict[str, int],
) -> None:
    failures.at(step, times=1)

    run_pipeline(pdf_resume)

    assert final_state(sync_db, pdf_resume) == expected_state(sync_db, pdf_resume)
    assert failures.ai.calls == ai_calls


@pytest.mark.parametrize("step", ["extract_profile", "score", "resolve_person", "save"])
def test_after_the_retries_run_out_the_failed_step_is_kept_and_a_rerun_completes(
    sync_db: Session, failures: Failures, pdf_resume: Resume, events: Events, step: str
) -> None:
    failures.at(step, times=Failures.FOREVER)

    run_pipeline(pdf_resume)

    resume = reload(sync_db, pdf_resume)
    assert (resume.status, resume.processing_step) == ("failed", step)
    assert resume.person_id is None  # the resolve/save transaction was rolled back every time
    assert events()[-1]["status"] == "failed"
    assert counts(sync_db) == {**dict.fromkeys(CLEAN_COUNTS, 0), "candidates": 1}
    # The retry-failed endpoint re-queues the AI task for a resume failed at an AI step.
    assert resume.processing_step in tasks.AI_STEPS

    failures.clear()
    analyse(resume)

    assert final_state(sync_db, pdf_resume) == expected_state(sync_db, pdf_resume)


def test_text_extraction_failure_keeps_its_step(
    sync_db: Session, failures: Failures, pdf_resume: Resume
) -> None:
    failures.at("extract_text", times=Failures.FOREVER)

    tasks.extract_resume.apply(args=(str(pdf_resume.id),))

    resume = reload(sync_db, pdf_resume)
    assert (resume.status, resume.processing_step) == ("failed", "extract_text")
    assert resume.processing_step not in tasks.AI_STEPS  # retry-failed starts from the PDF


# ── Quota (spec §5.5) ─────────────────────────────────────────
@pytest.fixture
def retry_countdowns() -> Iterator[list[float]]:
    """The countdown of every Celery retry the tasks schedule (eager mode runs them at once,
    but the requested delay is still reported)."""
    countdowns: list[float] = []

    def record(sender: Any = None, reason: Any = None, **_: Any) -> None:
        countdowns.append(reason.when)

    task_retry.connect(record, weak=False)
    yield countdowns
    task_retry.disconnect(record)


@pytest.mark.parametrize(
    ("step", "retry_after", "countdown"),
    [
        ("score", 42.0, 42.0),
        ("extract_profile", 17.5, 17.5),
        ("score", None, tasks.QUOTA_RETRY_FALLBACK_DELAY),
    ],
)
def test_quota_error_shows_at_once_then_retries_after_googles_delay(
    sync_db: Session,
    failures: Failures,
    monkeypatch: pytest.MonkeyPatch,
    events: Events,
    retry_countdowns: list[float],
    step: str,
    retry_after: float | None,
    countdown: float,
) -> None:
    campaign = make_campaign(sync_db)
    resume = make_resume(sync_db, campaign, resume_text())
    failures.ai.error = lambda: AIQuotaExceeded("429 quota", retry_after=retry_after)
    failures.at(step, times=1)

    # What the database says when the quota event goes out (the UI reads both).
    seen: list[tuple[str, str | None]] = []
    real_publish = tasks.publish_resume_event

    def publish(resume_id: str, status: str, extra: Any = None) -> None:
        if status == "quota_exceeded":
            current = reload(sync_db, resume)
            seen.append((current.status, current.processing_step))
        real_publish(resume_id, status, extra)

    monkeypatch.setattr(tasks, "publish_resume_event", publish)

    analyse(resume)

    assert seen == [("quota_exceeded", step)]
    assert retry_countdowns == [countdown]
    assert [e["status"] for e in events()] == ["quota_exceeded", "done"]
    assert final_state(sync_db, resume)["resume"] == ("done", None, None)
    assert counts(sync_db) == CLEAN_COUNTS


def test_quota_retry_delay_prefers_the_providers_delay() -> None:
    assert tasks.quota_retry_delay(AIQuotaExceeded("x", retry_after=12.5)) == 12.5
    assert tasks.quota_retry_delay(AIQuotaExceeded("x")) == tasks.QUOTA_RETRY_FALLBACK_DELAY
    assert tasks.quota_retry_delay(AIQuotaExceeded("x", retry_after=0)) == (
        tasks.QUOTA_RETRY_FALLBACK_DELAY
    )


# ── Mapping ───────────────────────────────────────────────────
def test_person_profile_maps_the_extraction() -> None:
    extraction = ExtractionResult(
        name="Asha Rao",
        email=ASHA_EMAIL,
        phone=ASHA_PHONE,
        github_url="https://github.com/asha",
        linkedin_url="https://linkedin.com/in/asha",
        skills=["Python"],
        education=[{"degree": "B.Tech"}],
        work_experience=[{"role": "Engineer"}],
        certifications=["AWS Certified"],
        projects=[{"name": "Parser"}],
        years_of_experience=6.0,
    )

    profile = tasks.person_profile(extraction)

    assert profile.model_dump() == {
        "full_name": "Asha Rao",
        "github_url": "https://github.com/asha",
        "linkedin_url": "https://linkedin.com/in/asha",
        "skills": ["Python"],
        "education": [{"degree": "B.Tech"}],
        "work_experience": [{"role": "Engineer"}],
        "certifications": ["AWS Certified"],
        "projects": [{"name": "Parser"}],
        "years_of_experience": 6.0,
    }
