"""Seed a local database with legacy candidates, and write the results the backfill must produce.

Usage (from ``backend/``, or inside the backend container)::

    python -m app.devtools.seed --people 800 --campaigns 15 --seed 42 [--manifest PATH] [--reset]

What it does
    Builds a ground truth **in memory** first: ``--people`` humans, each with canonical contact
    details and a profile, and the applications each one makes. Then it emits that truth in the
    **current** (legacy) schema: ``--campaigns`` campaigns, and for every application one
    ``resumes`` row (status ``done``, plausible filename/size/hash/storage path; no files are
    uploaded) and one ``candidates`` row (contact details as *written* on that resume, skills,
    experience, scoring, category, ...). Finally it writes a JSON manifest (default
    ``backend/seed-manifest.json``, gitignored) with the counts it created and the counts the
    Sprint 3 backfill is **expected** to produce (spec ``docs/specs/EPIC-00-platform-core.md``
    §6.4, verification in §6.3).

Safety
    * Refuses to run when ``APP_ENV=production`` (``SeedRefused``).
    * Refuses when any ``candidates`` row exists, unless ``--reset`` is given. ``--reset``
      deletes **only** the rows of ``candidates``, ``resumes`` and ``campaigns``, prints how many
      it deleted, and runs in the same transaction as the seeding (all or nothing).
      ``--if-empty`` instead exits successfully without changes (used by ``make setup``).
    * Uses ``settings.SYNC_DATABASE_URL``; the core ``generate(session, ...)`` takes any sync
      ``Session``, and only flushes, so the caller owns the transaction (tests use
      ``AsyncSession.run_sync`` inside their rolled-back transaction).

Determinism
    All randomness comes from one ``random.Random(seed)`` and one ``Faker("en_IN")`` seeded with
    ``seed_instance(seed)``; global random state is neither used nor changed. UUIDs are drawn
    from that ``Random`` and every timestamp is ``BASE_TIME`` plus an offset, so the same
    arguments produce the same rows and a byte-identical manifest. Nothing depends on ``now()``.

Deliberate cases (spec §6.4) and their sizes
    Each case gets ``max(1, round(people * share))`` humans (conflicts: pairs of humans with
    ``max(1, round(people * 0.005))`` pairs); every remaining human is ``plain``. For the
    defaults (800 people) that is 24 humans per 3 % case, 8 for ``outlook_plus_tag``, 80 for
    ``multi_campaign``, 4 conflict pairs and 560 plain humans (1,125 legacy candidates).

    ==================== ======= ===========================================================
    case                 share   what each human submits
    ==================== ======= ===========================================================
    plain                rest    1 resume; email + phone, written in canonical form
    email_case           3 %     2 resumes, 2 campaigns: ``asha.rao12@yahoo.com`` and
                                 ``Asha.Rao12@Yahoo.com``; no phone
    gmail_dots           3 %     2 resumes, 2 campaigns: ``asha.rao12@gmail.com`` and
                                 ``a.sharao.12@googlemail.com`` (canonical
                                 ``asharao12@gmail.com``); no phone
    gmail_plus_tag       3 %     2 resumes, 2 campaigns: ``asharao12@gmail.com`` and
                                 ``asharao12+jobs@gmail.com``; no phone
    outlook_plus_tag     1 %     2 resumes, 2 campaigns: ``asha.rao12@outlook.com`` and
                                 ``asha.rao12+jobs@outlook.com`` (dots are kept); no phone
    phone_formats        3 %     3 resumes, 3 campaigns: ``+91 98765 43210``,
                                 ``09876543210``, ``98765-43210``; no email
    multi_campaign       10 %    2-4 resumes (bounded by ``--campaigns``) in distinct
                                 campaigns, identical contact details
    same_campaign_twice  3 %     2 resumes in the **same** campaign, identical contact details
    email_phone_conflict pairs   humans A and B, 1 resume each (different campaigns), then
                                 a 3rd resume by A, in a campaign A had not applied to, with
                                 A's email and **B's phone**
    no_contact           3 %     no email and no phone; every 3rd such human (index 0, 3, ...)
                                 applies to 2 campaigns, the others to 1
    ==================== ======= ===========================================================

    Contact details never collide by accident: every email local part and phone number is
    derived from the human's unique ordinal (``first.last<ordinal>`` / ``98`` followed by
    ``(ordinal * 7919 + 4321) mod 10**8``, a bijection), so two different humans never share a
    canonical email or phone except in the deliberate conflict case. All phones are 10-digit
    Indian mobiles starting ``98``, valid for ``PHONE_DEFAULT_REGION=IN``; all emails are
    syntactically valid. Emails are written in canonical form except in the variant cases above.

How each expected number is derived (no normalisation or matching code is run)
    The generator assigns every submission the Person key the §5.3 rules must resolve it to:

    * Has an email and/or phone, and the identifiers are the human's own: the human's key.
      Every variant above normalises (§5.2: trim + lowercase; gmail/googlemail drop dots and
      ``+tag`` and become ``gmail.com``; outlook/hotmail/live/icloud drop ``+tag`` only;
      phones become E.164) to that human's canonical value, so all of them match one Person.
    * Conflict resume (A's email, B's phone): A's key (attach to the **email** match) and one
      expected ``identity_conflicts`` row. Conflict resumes are emitted **last** (latest
      ``created_at``), after A's and B's own resumes, so both People already exist when the
      backfill (ordered by ``created_at, id``, §6.2) reaches them.
    * No email and no phone: a key of its own per resume (``P0123/R2``), because every
      identifier-less resume creates a new Person; such duplicates stay separate until E6.

    Then:

    * ``expected.people`` = number of distinct expected Person keys
      = (humans with contact details) + (resumes without contact details).
    * ``expected.applications`` = number of distinct ``(campaign, expected Person)`` pairs
      (``UNIQUE(campaign_id, person_id)``). Only ``same_campaign_twice`` collapses two resumes
      into one application.
    * ``expected.identity_conflicts`` = number of conflict resumes (one per pair).
    * ``expected.legacy_candidate_map_rows`` = legacy candidates - expected applications (the
      older candidate IDs the backfill redirects, §6.2).
    * ``counts.*`` = rows inserted: one ``resumes`` and one ``candidates`` row per submission,
      and ``--campaigns`` campaigns. ``counts.true_people`` is ``--people``; it differs from
      ``expected.people`` only by the extra no-contact resumes.

    ``cases.<name>`` repeats these numbers per case. Cases share no Person, so per-case values
    sum to the totals. ``cases.<name>.examples`` lists the candidates of the first human of the
    case (IDs and contact details as written) so a reviewer or test can find them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from faker import Faker
from sqlalchemy import create_engine, delete, func, insert, select
from sqlalchemy.orm import Session

from app.modules.hiring.models import Campaign, Candidate
from app.modules.ingestion.models import Resume
from app.platform.config import settings

BASE_TIME = datetime(2025, 1, 6, 9, 0, tzinfo=UTC)
DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / "seed-manifest.json"
MANIFEST_VERSION = 1
MIN_CAMPAIGNS = 3  # phone_formats needs three distinct campaigns

# ── Cases ────────────────────────────────────────────────────
PLAIN = "plain"
EMAIL_CASE = "email_case"
GMAIL_DOTS = "gmail_dots"
GMAIL_PLUS_TAG = "gmail_plus_tag"
OUTLOOK_PLUS_TAG = "outlook_plus_tag"
PHONE_FORMATS = "phone_formats"
MULTI_CAMPAIGN = "multi_campaign"
SAME_CAMPAIGN_TWICE = "same_campaign_twice"
EMAIL_PHONE_CONFLICT = "email_phone_conflict"
NO_CONTACT = "no_contact"

CASE_SHARES: dict[str, float] = {
    EMAIL_CASE: 0.03,
    GMAIL_DOTS: 0.03,
    GMAIL_PLUS_TAG: 0.03,
    OUTLOOK_PLUS_TAG: 0.01,
    PHONE_FORMATS: 0.03,
    MULTI_CAMPAIGN: 0.10,
    SAME_CAMPAIGN_TWICE: 0.03,
    NO_CONTACT: 0.03,
}
CONFLICT_PAIR_SHARE = 0.005
ALL_CASES = (PLAIN, *CASE_SHARES, EMAIL_PHONE_CONFLICT)

# Domains that §5.2 leaves untouched (besides trim + lowercase).
PLAIN_DOMAINS = ("yahoo.com", "yahoo.co.in", "rediffmail.com", "protonmail.com", "zoho.com")
PLUS_TAG_DOMAINS = ("outlook.com", "hotmail.com", "live.com", "icloud.com")
TAGS = ("jobs", "careers", "apply", "naukri")

PhoneWriter = Callable[[str], str]
# The three formats of spec §6.4, for a 10-digit national number ``n``.
SPEC_PHONE_FORMATS: tuple[PhoneWriter, ...] = (
    lambda n: f"+91 {n[:5]} {n[5:]}",
    lambda n: f"0{n}",
    lambda n: f"{n[:5]}-{n[5:]}",
)
ALL_PHONE_FORMATS: tuple[PhoneWriter, ...] = (
    *SPEC_PHONE_FORMATS,
    lambda n: n,
    lambda n: f"+91{n}",
)

# ── Profile vocabulary ───────────────────────────────────────
ROLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Backend Engineer", ("Python", "FastAPI", "PostgreSQL", "Docker", "Redis", "Celery")),
    ("Frontend Engineer", ("TypeScript", "React", "Next.js", "CSS", "Jest", "GraphQL")),
    ("Data Engineer", ("Python", "SQL", "Spark", "Airflow", "Kafka", "dbt")),
    ("ML Engineer", ("Python", "PyTorch", "scikit-learn", "MLOps", "LLMs", "SQL")),
    ("DevOps Engineer", ("Kubernetes", "Terraform", "AWS", "Docker", "Linux", "Prometheus")),
    ("Android Developer", ("Kotlin", "Java", "Jetpack Compose", "Android SDK", "Firebase")),
    ("QA Engineer", ("Selenium", "Python", "Pytest", "Cypress", "API Testing", "Jira")),
    ("Full Stack Developer", ("JavaScript", "Node.js", "React", "MongoDB", "Express", "AWS")),
    ("Java Developer", ("Java", "Spring Boot", "Microservices", "MySQL", "Kafka", "Docker")),
    ("Data Analyst", ("SQL", "Excel", "Power BI", "Tableau", "Python", "Statistics")),
)
GENERAL_SKILLS = ("Git", "Agile", "REST APIs", "Communication", "Linux", "CI/CD")
DEGREES = ("B.Tech Computer Science", "B.E. Information Technology", "MCA", "M.Tech", "B.Sc")
CERTIFICATIONS = ("AWS Certified Developer", "CKA", "Google Data Analytics", "Scrum Master")
PIPELINE_STAGES: tuple[str | None, ...] = (
    *([None] * 6),
    "screened",
    "phone_call",
    "technical",
    "offer",
    "hired",
    "rejected_manual",
)
RECOMMENDATIONS = {
    "strong_match": "Proceed to technical interview",
    "moderate_match": "Phone screen first",
    "weak_match": "Phone screen first",
    "rejected": "Not recommended",
}


class SeedRefused(RuntimeError):
    """The generator will not run against this database or environment."""


@dataclass
class Human:
    """Ground truth: one real person, with canonical (already §5.2-normal) identifiers."""

    key: str
    case: str
    ordinal: int
    first: str
    last: str
    full_name: str
    email: str | None
    phone: str | None
    profile: dict[str, Any]


@dataclass
class Submission:
    """One resume a human submitted to one campaign; becomes one resume + one candidate row."""

    human: Human
    campaign: int
    email: str | None
    phone: str | None
    expected_person: str
    conflict_with: str | None = None
    late: bool = False
    version: int = 1
    resume_row: dict[str, Any] = field(default_factory=dict)
    candidate_row: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    people: int
    campaigns: int
    seed: int
    campaign_rows: list[dict[str, Any]]
    humans: list[Human]
    submissions: list[Submission]


# ── Plan (pure, in memory) ───────────────────────────────────
def case_sizes(people: int) -> dict[str, int]:
    """Humans per case (conflict: humans, i.e. 2 per pair). Raises if ``people`` is too small."""
    sizes = {case: max(1, round(people * share)) for case, share in CASE_SHARES.items()}
    sizes[EMAIL_PHONE_CONFLICT] = 2 * max(1, round(people * CONFLICT_PAIR_SHARE))
    plain = people - sum(sizes.values())
    if plain < 1:
        raise ValueError(f"--people {people} is too small to include every case")
    return {PLAIN: plain, **sizes}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower()) or "x"


def _uuid(rng: random.Random) -> uuid.UUID:
    return uuid.UUID(int=rng.getrandbits(128), version=4)


def _national_phone(ordinal: int) -> str:
    """A unique 10-digit Indian mobile number for ``ordinal`` (bijective below 10**8)."""
    return f"98{(ordinal * 7919 + 4321) % 10**8:08d}"


def _mixed_case(email: str) -> str:
    local, domain = email.split("@")
    return ".".join(p.capitalize() for p in local.split(".")) + "@" + domain.capitalize()


class _Builder:
    def __init__(self, people: int, campaigns: int, seed: int) -> None:
        if campaigns < MIN_CAMPAIGNS:
            raise ValueError(f"--campaigns must be at least {MIN_CAMPAIGNS}")
        self.people = people
        self.campaigns = campaigns
        self.seed = seed
        self.rng = random.Random(seed)
        self.fake = Faker("en_IN")
        self.fake.seed_instance(seed)
        self.humans: list[Human] = []
        self.subs: list[Submission] = []

    # humans ----------------------------------------------------
    def human(self, case: str, email_style: str | None, with_phone: bool) -> Human:
        ordinal = len(self.humans) + 1
        first, last = self.fake.first_name(), self.fake.last_name()
        f, la = _slug(first), _slug(last)
        email: str | None
        if email_style is None:
            email = None
        elif email_style == "gmail":
            email = f"{f}{la}{ordinal}@gmail.com"
        elif email_style == "plus_tag":
            email = f"{f}.{la}{ordinal}@{self.rng.choice(PLUS_TAG_DOMAINS)}"
        else:
            email = f"{f}.{la}{ordinal}@{self.rng.choice(PLAIN_DOMAINS)}"
        phone = f"+91{_national_phone(ordinal)}" if with_phone else None
        has_links = email is not None or phone is not None
        human = Human(
            key=f"P{ordinal:04d}",
            case=case,
            ordinal=ordinal,
            first=f,
            last=la,
            full_name=f"{first} {last}",
            email=email,
            phone=phone,
            profile=self.profile(f, la, ordinal, has_links),
        )
        self.humans.append(human)
        return human

    def profile(self, first: str, last: str, ordinal: int, links: bool) -> dict[str, Any]:
        rng = self.rng
        role, pool = rng.choice(ROLES)
        years = round(rng.uniform(0.5, 14.0), 1)
        start = 2024 - max(1, int(years))
        handle = f"{first}{last}{ordinal}"
        return {
            "role": role,
            "skills": rng.sample(pool, rng.randint(3, len(pool)))
            + rng.sample(GENERAL_SKILLS, rng.randint(0, 2)),
            "years_of_experience": years,
            "education": [
                {
                    "degree": rng.choice(DEGREES),
                    "institution": f"{self.fake.city()} Institute of Technology",
                    "year": str(start - rng.randint(0, 2)),
                }
            ],
            "work_experience": [
                {"title": role, "company": self.fake.company(), "duration": f"{start}-2024"}
            ],
            "certifications": rng.sample(CERTIFICATIONS, rng.randint(0, 2)),
            "projects": [
                {
                    "name": f"{rng.choice(pool)} {rng.choice(('dashboard', 'service', 'pipeline'))}",
                    "description": f"Built with {', '.join(rng.sample(pool, 2))}",
                }
            ],
            "github_url": f"https://github.com/{handle}" if links else None,
            "linkedin_url": f"https://www.linkedin.com/in/{handle}" if links else None,
        }

    # submissions -----------------------------------------------
    def submit(
        self,
        human: Human,
        campaign: int,
        email: str | None,
        phone: str | None,
        **extra: Any,
    ) -> Submission:
        sub = Submission(human, campaign, email, phone, expected_person=human.key, **extra)
        self.subs.append(sub)
        return sub

    def distinct_campaigns(self, k: int) -> list[int]:
        return self.rng.sample(range(self.campaigns), k)

    def build(self) -> Plan:
        rng = self.rng
        sizes = case_sizes(self.people)
        campaign_rows = self.campaign_rows()

        for _ in range(sizes[PLAIN]):
            h = self.human(PLAIN, "plain", with_phone=True)
            phone = rng.choice(ALL_PHONE_FORMATS)(_national_phone(h.ordinal))
            self.submit(h, rng.randrange(self.campaigns), h.email, phone)

        for _ in range(sizes[EMAIL_CASE]):
            h = self.human(EMAIL_CASE, "plain", with_phone=False)
            assert h.email is not None
            for c, written in zip(
                self.distinct_campaigns(2), (h.email, _mixed_case(h.email)), strict=True
            ):
                self.submit(h, c, written, None)

        for _ in range(sizes[GMAIL_DOTS]):
            h = self.human(GMAIL_DOTS, "gmail", with_phone=False)
            dotted = f"{h.first}.{h.last}{h.ordinal}@gmail.com"
            split = f"{h.first[0]}.{h.first[1:]}{h.last}.{h.ordinal}@googlemail.com"
            for c, written in zip(self.distinct_campaigns(2), (dotted, split), strict=True):
                self.submit(h, c, written, None)

        for _ in range(sizes[GMAIL_PLUS_TAG]):
            h = self.human(GMAIL_PLUS_TAG, "gmail", with_phone=False)
            assert h.email is not None
            local, domain = h.email.split("@")
            tagged = f"{local}+{rng.choice(TAGS)}@{domain}"
            for c, written in zip(self.distinct_campaigns(2), (h.email, tagged), strict=True):
                self.submit(h, c, written, None)

        for _ in range(sizes[OUTLOOK_PLUS_TAG]):
            h = self.human(OUTLOOK_PLUS_TAG, "plus_tag", with_phone=False)
            assert h.email is not None
            local, domain = h.email.split("@")
            tagged = f"{local}+{rng.choice(TAGS)}@{domain}"
            for c, written in zip(self.distinct_campaigns(2), (h.email, tagged), strict=True):
                self.submit(h, c, written, None)

        for _ in range(sizes[PHONE_FORMATS]):
            h = self.human(PHONE_FORMATS, None, with_phone=True)
            national = _national_phone(h.ordinal)
            for c, fmt in zip(self.distinct_campaigns(3), SPEC_PHONE_FORMATS, strict=True):
                self.submit(h, c, None, fmt(national))

        for _ in range(sizes[MULTI_CAMPAIGN]):
            h = self.human(MULTI_CAMPAIGN, "plain", with_phone=True)
            phone = rng.choice(ALL_PHONE_FORMATS)(_national_phone(h.ordinal))
            k = rng.randint(2, min(4, self.campaigns))
            for c in self.distinct_campaigns(k):
                self.submit(h, c, h.email, phone)

        for _ in range(sizes[SAME_CAMPAIGN_TWICE]):
            h = self.human(SAME_CAMPAIGN_TWICE, "plain", with_phone=True)
            phone = rng.choice(ALL_PHONE_FORMATS)(_national_phone(h.ordinal))
            c = rng.randrange(self.campaigns)
            self.submit(h, c, h.email, phone, version=1)
            self.submit(h, c, h.email, phone, version=2)

        for _ in range(sizes[EMAIL_PHONE_CONFLICT] // 2):
            a = self.human(EMAIL_PHONE_CONFLICT, "plain", with_phone=True)
            b = self.human(EMAIL_PHONE_CONFLICT, "plain", with_phone=True)
            ca, cb = self.distinct_campaigns(2)
            self.submit(a, ca, a.email, a.phone)
            self.submit(b, cb, b.email, b.phone)
            other = rng.choice([c for c in range(self.campaigns) if c != ca])
            b_phone = SPEC_PHONE_FORMATS[1](_national_phone(b.ordinal))
            self.submit(a, other, a.email, b_phone, conflict_with=b.key, late=True)

        for i in range(sizes[NO_CONTACT]):
            h = self.human(NO_CONTACT, None, with_phone=False)
            for n, c in enumerate(self.distinct_campaigns(2 if i % 3 == 0 else 1), start=1):
                sub = self.submit(h, c, None, None, version=n)
                sub.expected_person = f"{h.key}/R{n}"

        # Emission order: shuffled, then the conflict resumes last (see module docstring).
        ordered = [s for s in self.subs if not s.late]
        rng.shuffle(ordered)
        ordered += [s for s in self.subs if s.late]
        for position, sub in enumerate(ordered):
            self.rows(sub, position, campaign_rows[sub.campaign])
        return Plan(self.people, self.campaigns, self.seed, campaign_rows, self.humans, ordered)

    # rows ------------------------------------------------------
    def campaign_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for i in range(self.campaigns):
            role, skills = ROLES[i % len(ROLES)]
            created = BASE_TIME + timedelta(hours=i)
            rows.append(
                {
                    "id": _uuid(self.rng),
                    "title": f"{role} - Hiring Round {i // len(ROLES) + 1}",
                    "role": role,
                    "description": f"We are hiring a {role} to join our {self.fake.city()} team.",
                    "required_skills": list(skills[:4]),
                    "status": "closed" if i % 5 == 4 else "active",
                    "created_at": created,
                    "updated_at": created,
                }
            )
        return rows

    def rows(self, sub: Submission, position: int, campaign: dict[str, Any]) -> None:
        rng = self.rng
        h, p = sub.human, sub.human.profile
        created = BASE_TIME + timedelta(days=1, minutes=7 * position)
        resume_id, candidate_id = _uuid(rng), _uuid(rng)
        suffix = "" if sub.version == 1 else f"_v{sub.version}"
        filename = f"{h.first}_{h.last}_resume{suffix}.pdf"
        sub.resume_row = {
            "id": resume_id,
            "campaign_id": campaign["id"],
            "original_filename": filename,
            "file_hash": hashlib.sha256(f"seed:{self.seed}:resume:{position}".encode()).hexdigest(),
            "storage_path": f"campaigns/{campaign['id']}/resumes/{resume_id}/{filename}",
            "file_size_bytes": rng.randint(30_000, 900_000),
            "status": "done",
            "retry_count": 0,
            "error_message": None,
            "celery_task_id": None,
            "uploaded_at": created - timedelta(minutes=2),
            "updated_at": created,
        }

        required: list[str] = campaign["required_skills"]
        skills: list[str] = p["skills"]
        missing = [s for s in required if s not in skills]
        years: float = p["years_of_experience"]
        breakdown = {
            "skills_match": round(40 * (len(required) - len(missing)) / len(required)),
            "experience_match": min(30, round(years * 4)),
            "project_relevance": rng.randint(2, 15),
            "education_bonus": rng.randint(2, 10),
            "communication_quality": rng.randint(1, 5),
        }
        score = float(sum(breakdown.values()))
        category = (
            "strong_match"
            if score >= 75
            else "moderate_match"
            if score >= 50
            else "weak_match"
            if score >= 25
            else "rejected"
        )
        sub.candidate_row = {
            "id": candidate_id,
            "resume_id": resume_id,
            "campaign_id": campaign["id"],
            "name": h.full_name,
            "email": sub.email,
            "phone": sub.phone,
            "github_url": p["github_url"],
            "linkedin_url": p["linkedin_url"],
            "skills": skills,
            "education": p["education"],
            "work_experience": p["work_experience"],
            "certifications": p["certifications"],
            "projects": p["projects"],
            "years_of_experience": years,
            "summary": f"{p['role']} with {years} years of experience in {', '.join(skills[:3])}.",
            "strengths": [f"Hands-on {s}" for s in skills[:2]],
            "weaknesses": [f"No {s} experience" for s in missing[:2]] or ["Limited leadership"],
            "missing_skills": missing,
            "recommendation": RECOMMENDATIONS[category],
            "score": score,
            "score_breakdown": breakdown,
            "category": category,
            "notes": None,
            "pipeline_stage": rng.choice(PIPELINE_STAGES),
            "raw_text": f"{h.full_name}\n{p['role']}\nSkills: {', '.join(skills)}",
            "created_at": created,
            "updated_at": created,
        }


def build_plan(people: int, campaigns: int, seed: int) -> Plan:
    """The full ground truth and every row to insert, without touching a database."""
    return _Builder(people, campaigns, seed).build()


# ── Manifest ─────────────────────────────────────────────────
def _expected(subs: Sequence[Submission]) -> dict[str, int]:
    return {
        "people": len({s.expected_person for s in subs}),
        "applications": len({(s.campaign, s.expected_person) for s in subs}),
        "identity_conflicts": sum(1 for s in subs if s.conflict_with is not None),
    }


def build_manifest(plan: Plan) -> dict[str, Any]:
    """Counts and expected backfill results, derived from the plan (see module docstring)."""
    subs = plan.submissions
    expected = _expected(subs)
    expected["legacy_candidate_map_rows"] = len(subs) - expected["applications"]
    cases: dict[str, Any] = {}
    for case in ALL_CASES:
        case_subs = [s for s in subs if s.human.case == case]
        humans = [h for h in plan.humans if h.case == case]
        first = humans[0]
        examples = [
            {
                "candidate_id": str(s.candidate_row["id"]),
                "campaign_id": str(s.candidate_row["campaign_id"]),
                "email": s.email,
                "phone": s.phone,
                "expected_person": s.expected_person,
                **({"conflict_with": s.conflict_with} if s.conflict_with else {}),
            }
            for s in case_subs
            if s.human is first or (case == EMAIL_PHONE_CONFLICT and s.human is humans[1])
        ]
        cases[case] = {
            "true_people": len(humans),
            "candidates": len(case_subs),
            **{f"expected_{k}": v for k, v in _expected(case_subs).items()},
            "examples": examples,
        }
    return {
        "manifest_version": MANIFEST_VERSION,
        "generator": "app.devtools.seed",
        "rules": "docs/specs/EPIC-00-platform-core.md §5.2-5.3, docs/adr/0004",
        "params": {"people": plan.people, "campaigns": plan.campaigns, "seed": plan.seed},
        "counts": {
            "campaigns": len(plan.campaign_rows),
            "resumes": len(subs),
            "legacy_candidates": len(subs),
            "true_people": len(plan.humans),
        },
        "expected": expected,
        "cases": cases,
    }


# ── Database ─────────────────────────────────────────────────
def ensure_not_production() -> None:
    if settings.APP_ENV.strip().lower() == "production":
        raise SeedRefused("refusing to seed: APP_ENV=production")


def existing_counts(session: Session) -> dict[str, int]:
    return {
        model.__tablename__: session.scalar(select(func.count()).select_from(model)) or 0
        for model in (Campaign, Resume, Candidate)
    }


def reset(session: Session) -> dict[str, int]:
    """Delete every candidate, resume and campaign row (only those tables). Returns the counts."""
    ensure_not_production()
    counts = existing_counts(session)
    for model in (Candidate, Resume, Campaign):
        session.execute(delete(model))
    session.flush()
    return counts


def generate(session: Session, *, people: int, campaigns: int, seed: int) -> dict[str, Any]:
    """Insert the seed rows through ``session`` (flush only, no commit); return the manifest."""
    ensure_not_production()
    if existing_counts(session)["candidates"]:
        raise SeedRefused("refusing to seed: candidates already exist (use --reset)")
    plan = build_plan(people, campaigns, seed)
    session.execute(insert(Campaign), plan.campaign_rows)
    session.execute(insert(Resume), [s.resume_row for s in plan.submissions])
    session.execute(insert(Candidate), [s.candidate_row for s in plan.submissions])
    session.flush()
    return build_manifest(plan)


def write_manifest(manifest: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ── CLI ──────────────────────────────────────────────────────
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.devtools.seed",
        description="Seed legacy campaigns/resumes/candidates and write the expected-results manifest.",
    )
    parser.add_argument("--people", type=int, default=800)
    parser.add_argument("--campaigns", type=int, default=15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--reset",
        action="store_true",
        help="delete ALL campaigns, resumes and candidates first",
    )
    group.add_argument(
        "--if-empty",
        action="store_true",
        help="do nothing (exit 0) if candidates already exist",
    )
    args = parser.parse_args(argv)

    started = time.perf_counter()
    try:
        ensure_not_production()
        case_sizes(args.people)  # validate sizes before touching the database
        engine = create_engine(settings.SYNC_DATABASE_URL)
        with Session(engine) as session:
            before = existing_counts(session)
            if before["candidates"] and args.if_empty:
                print(f"seed: skipped, {before['candidates']} candidates already exist")
                return 0
            if args.reset:
                deleted = reset(session)
                print(
                    "seed: --reset deleted "
                    f"{deleted['candidates']} candidates, {deleted['resumes']} resumes, "
                    f"{deleted['campaigns']} campaigns"
                )
            manifest = generate(
                session, people=args.people, campaigns=args.campaigns, seed=args.seed
            )
            session.commit()
        engine.dispose()
    except (SeedRefused, ValueError) as exc:
        print(f"seed: {exc}", file=sys.stderr)
        return 1

    write_manifest(manifest, args.manifest)
    counts, expected = manifest["counts"], manifest["expected"]
    print(
        f"seed: inserted {counts['campaigns']} campaigns, {counts['resumes']} resumes, "
        f"{counts['legacy_candidates']} candidates in {time.perf_counter() - started:.1f}s"
    )
    print(
        f"seed: expected after backfill: {expected['people']} people, "
        f"{expected['applications']} applications, "
        f"{expected['identity_conflicts']} identity conflicts"
    )
    print(f"seed: manifest written to {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
