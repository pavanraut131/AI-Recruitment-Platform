"""E0-12: the seed generator inserts what its manifest says, deterministically, with every §6.4 case.

Since E0-20 it stores processed resumes and runs them through the pipeline's matching and
scoring services (``talent.resolve_person``, ``hiring.upsert_application``), so the manifest's
expectations, derived from the ground truth without any matching code, are checked against what
that code actually produced (``verify_people_applications``).

The generator runs inside the test's rolled-back transaction through ``AsyncSession.run_sync``,
so nothing it inserts outlives the test.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.devtools import seed
from app.modules.hiring.models import Application, Campaign, LegacyCandidateMap
from app.modules.ingestion import verify_people_applications
from app.modules.ingestion.models import Resume
from app.modules.talent.models import IdentityConflict, Person
from app.platform.config import settings
from tests.factories import make_candidate

PEOPLE, CAMPAIGNS, SEED = 60, 4, 7


async def run_seed(db: AsyncSession, **overrides: Any) -> dict[str, Any]:
    kwargs = {"people": PEOPLE, "campaigns": CAMPAIGNS, "seed": SEED, **overrides}
    manifest: dict[str, Any] = await db.run_sync(seed.generate, **kwargs)
    return manifest


async def count(db: AsyncSession, model: Any) -> int:
    return (await db.scalar(select(func.count()).select_from(model))) or 0


def digits(phone: str) -> str:
    """The last 10 digits: the national number of an Indian mobile in any of the formats."""
    return re.sub(r"\D", "", phone)[-10:]


def manifest_for(people: int = PEOPLE, campaigns: int = CAMPAIGNS, seed_: int = SEED) -> Any:
    return seed.build_manifest(seed.build_plan(people, campaigns, seed_))


def contact(resume: Resume) -> tuple[str | None, str | None]:
    """The email and phone as written on the resume (its extraction snapshot)."""
    extracted = resume.extracted or {}
    return extracted.get("email"), extracted.get("phone")


# ── Against the database ─────────────────────────────────────
async def test_row_counts_match_the_manifest(db_session: AsyncSession) -> None:
    manifest = await run_seed(db_session)

    counts, expected = manifest["counts"], manifest["expected"]
    assert await count(db_session, Campaign) == counts["campaigns"] == CAMPAIGNS
    assert await count(db_session, Resume) == counts["resumes"]
    assert counts["true_people"] == PEOPLE
    done = await db_session.scalar(
        select(func.count())
        .select_from(Resume)
        .where(Resume.status == "done", Resume.person_id.is_not(None))
    )
    assert done == counts["resumes"]
    no_contact = sum(
        1 for r in (await db_session.scalars(select(Resume))).all() if contact(r) == (None, None)
    )
    assert no_contact == manifest["cases"][seed.NO_CONTACT]["resumes"]
    # What the matching code produced is what the ground truth says it must.
    assert await count(db_session, Person) == expected["people"]
    assert await count(db_session, Application) == expected["applications"]
    assert await count(db_session, IdentityConflict) == expected["identity_conflicts"]
    assert await count(db_session, LegacyCandidateMap) == 0


async def test_the_seed_passes_verify_people_applications_with_its_manifest(
    db_session: AsyncSession,
) -> None:
    manifest = await run_seed(db_session)
    report = await db_session.run_sync(
        lambda db: verify_people_applications.verify(db, manifest=manifest, default_region="IN")
    )
    assert report.ok, report.render()
    assert [key for key, _, _ in report.manifest] == [
        "expected.people",
        "expected.applications",
        "expected.identity_conflicts",
    ]


async def test_applications_take_the_newest_resume_and_stage_and_their_history_times(
    db_session: AsyncSession,
) -> None:
    await run_seed(db_session)
    plan = seed.build_plan(PEOPLE, CAMPAIGNS, SEED)
    twice = [s for s in plan.submissions if s.human.case == seed.SAME_CAMPAIGN_TWICE]
    first_human = twice[0].human
    older, newer = sorted(
        (s for s in twice if s.human is first_human), key=lambda s: s.resume_row["uploaded_at"]
    )
    application = (
        await db_session.scalars(
            select(Application).where(Application.resume_id == newer.resume_row["id"])
        )
    ).one()
    assert newer.scoring is not None
    assert (application.score, application.category) == (
        newer.scoring.score,
        newer.scoring.category,
    )
    stages = [s.pipeline_stage for s in (older, newer) if s.pipeline_stage is not None]
    assert application.pipeline_stage == (stages[-1] if stages else None)
    assert application.created_at == older.resume_row["updated_at"]
    assert application.updated_at == newer.resume_row["updated_at"]
    # The application has the id drawn for the submission that created it (deterministic).
    assert application.id == older.application_id


async def test_the_database_run_matches_the_pure_plan(db_session: AsyncSession) -> None:
    """Same seed, same manifest: whether built in memory or inserted into the database."""
    manifest = await run_seed(db_session)
    assert json.dumps(manifest, sort_keys=True) == json.dumps(manifest_for(), sort_keys=True)


async def test_every_spec_case_is_in_the_database(db_session: AsyncSession) -> None:
    manifest = await run_seed(db_session)
    cases = manifest["cases"]
    assert set(cases) == set(seed.ALL_CASES)

    found: dict[str, list[Resume]] = {}
    for name, case in cases.items():
        assert case["true_people"] >= 1 and case["resumes"] >= 1, name
        rows = []
        for example in case["examples"]:
            row = await db_session.get(Resume, uuid.UUID(example["resume_id"]))
            assert row is not None, (name, example)
            assert str(row.campaign_id) == example["campaign_id"]
            assert contact(row) == (example["email"], example["phone"])
            rows.append(row)
        found[name] = rows

    def emails(name: str) -> list[str]:
        return [e for r in found[name] if (e := contact(r)[0]) is not None]

    def phones(name: str) -> list[str]:
        return [p for r in found[name] if (p := contact(r)[1]) is not None]

    # Emails differing only in case.
    a, b = emails(seed.EMAIL_CASE)
    assert a != b and a.lower() == b.lower()
    # Gmail dots (and googlemail), and +tag variants.
    a, b = emails(seed.GMAIL_DOTS)
    assert a != b
    assert {a.split("@")[1], b.split("@")[1]} == {"gmail.com", "googlemail.com"}
    assert a.split("@")[0].replace(".", "") == b.split("@")[0].replace(".", "")
    a, b = sorted(emails(seed.GMAIL_PLUS_TAG), key=len)
    assert b.endswith("@gmail.com") and "+" in b and b.split("+")[0] == a.split("@")[0]
    a, b = sorted(emails(seed.OUTLOOK_PLUS_TAG), key=len)
    assert "+" in b and b.split("+")[0] == a.split("@")[0] and "." in a.split("@")[0]
    # The three phone formats of the spec, all for one number, without an email.
    written = sorted(phones(seed.PHONE_FORMATS))
    assert len({digits(p) for p in written}) == 1
    national = digits(written[0])
    assert written == sorted(
        [f"+91 {national[:5]} {national[5:]}", f"0{national}", f"{national[:5]}-{national[5:]}"]
    )
    assert emails(seed.PHONE_FORMATS) == []
    # ... and the matching code made them one Person.
    assert len({r.person_id for r in found[seed.PHONE_FORMATS]}) == 1
    # Several campaigns; twice in one campaign (one Person, one application).
    assert len({r.campaign_id for r in found[seed.MULTI_CAMPAIGN]}) >= 2
    twice = found[seed.SAME_CAMPAIGN_TWICE]
    assert len(twice) == 2 and twice[0].campaign_id == twice[1].campaign_id
    assert twice[0].id != twice[1].id and twice[0].person_id == twice[1].person_id
    # Email/phone conflict: the last resume has A's email and B's phone.
    examples = cases[seed.EMAIL_PHONE_CONFLICT]["examples"]
    conflict = next(e for e in examples if "conflict_with" in e)
    owner = next(
        e
        for e in examples
        if e["expected_person"] == conflict["expected_person"] and "conflict_with" not in e
    )
    other = next(e for e in examples if e["expected_person"] == conflict["conflict_with"])
    assert conflict["email"] == owner["email"] != other["email"]
    assert digits(conflict["phone"]) == digits(other["phone"]) != digits(owner["phone"])
    # The conflict resume is uploaded (and resolved) after A's and B's own, and attached to A.
    by_id = {str(r.id): r for r in found[seed.EMAIL_PHONE_CONFLICT]}
    conflict_row = by_id[conflict["resume_id"]]
    assert by_id[owner["resume_id"]].uploaded_at < conflict_row.uploaded_at
    assert by_id[other["resume_id"]].uploaded_at < conflict_row.uploaded_at
    assert conflict_row.person_id == by_id[owner["resume_id"]].person_id
    # No contact details at all.
    assert all(contact(r) == (None, None) for r in found[seed.NO_CONTACT])


async def test_refuses_when_applications_exist(db_session: AsyncSession) -> None:
    await make_candidate(db_session)
    with pytest.raises(seed.SeedRefused, match="already exist"):
        await run_seed(db_session)


async def test_refuses_in_production(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    with pytest.raises(seed.SeedRefused, match="production"):
        await run_seed(db_session)
    with pytest.raises(seed.SeedRefused, match="production"):
        await db_session.run_sync(seed.reset)
    assert seed.main(["--people", "60", "--campaigns", "4"]) == 1
    assert await count(db_session, Application) == 0


async def test_reset_deletes_only_the_seeded_tables_and_reports_counts(
    db_session: AsyncSession,
) -> None:
    manifest = await run_seed(db_session)
    expected = manifest["expected"]
    deleted = await db_session.run_sync(seed.reset)
    assert deleted["campaigns"] == manifest["counts"]["campaigns"]
    assert deleted["resumes"] == manifest["counts"]["resumes"]
    assert deleted["applications"] == expected["applications"]
    assert deleted["derived"]["people"] == expected["people"]
    assert deleted["derived"]["identity_conflicts"] == expected["identity_conflicts"]
    assert deleted["derived"]["person_resumes"] == manifest["counts"]["resumes"]
    assert set(deleted["derived"]) == {m.__tablename__ for m in seed.DERIVED_MODELS}
    for model in (*seed.SEEDED_MODELS, *seed.DERIVED_MODELS):
        assert await count(db_session, model) == 0
    # After a reset the generator runs again.
    assert await run_seed(db_session) == manifest


async def test_on_batch_is_called_every_batch_and_at_the_end(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(seed, "BATCH_SIZE", 25)
    calls: list[int] = []

    def on_batch() -> None:
        calls.append(len(calls))

    manifest = await db_session.run_sync(
        lambda db: seed.generate(
            db, people=PEOPLE, campaigns=CAMPAIGNS, seed=SEED, on_batch=on_batch
        )
    )
    assert len(calls) == manifest["counts"]["resumes"] // 25 + 1


# ── Manifest (pure) ──────────────────────────────────────────
def test_same_seed_gives_an_identical_manifest_and_another_seed_does_not() -> None:
    assert json.dumps(manifest_for()) == json.dumps(manifest_for())
    assert manifest_for(seed_=SEED + 1) != manifest_for()


@pytest.mark.parametrize(("people", "campaigns"), [(PEOPLE, CAMPAIGNS), (800, 15)])
def test_manifest_is_internally_consistent(people: int, campaigns: int) -> None:
    manifest = manifest_for(people, campaigns)
    counts, expected, cases = manifest["counts"], manifest["expected"], manifest["cases"]

    assert manifest["params"] == {"people": people, "campaigns": campaigns, "seed": SEED}
    assert counts["campaigns"] == campaigns
    # Cases partition the people and the resumes, and their expectations add up.
    assert sum(c["true_people"] for c in cases.values()) == counts["true_people"] == people
    assert sum(c["resumes"] for c in cases.values()) == counts["resumes"]
    for key in ("people", "applications", "identity_conflicts"):
        assert sum(c[f"expected_{key}"] for c in cases.values()) == expected[key], key
    assert set(expected) == {"people", "applications", "identity_conflicts"}

    # The documented derivation (module docstring of app.devtools.seed).
    no_contact = cases[seed.NO_CONTACT]
    assert expected["people"] == people - no_contact["true_people"] + no_contact["resumes"]
    twice = cases[seed.SAME_CAMPAIGN_TWICE]
    assert twice["expected_applications"] == twice["true_people"] == twice["resumes"] // 2
    assert expected["applications"] == counts["resumes"] - twice["true_people"]
    conflicts = cases[seed.EMAIL_PHONE_CONFLICT]
    assert expected["identity_conflicts"] == conflicts["true_people"] // 2
    assert conflicts["resumes"] == 3 * (conflicts["true_people"] // 2)
    for name in seed.ALL_CASES:
        case = cases[name]
        if name not in (seed.NO_CONTACT, seed.SAME_CAMPAIGN_TWICE):
            assert case["expected_people"] == case["true_people"], name
            assert case["expected_applications"] == case["resumes"], name
        if name != seed.EMAIL_PHONE_CONFLICT:
            assert case["expected_identity_conflicts"] == 0, name
    assert cases[seed.EMAIL_CASE]["resumes"] == 2 * cases[seed.EMAIL_CASE]["true_people"]
    assert cases[seed.PHONE_FORMATS]["resumes"] == 3 * cases[seed.PHONE_FORMATS]["true_people"]
    multi = cases[seed.MULTI_CAMPAIGN]
    assert multi["resumes"] >= 2 * multi["true_people"]


@pytest.mark.parametrize(("people", "campaigns"), [(PEOPLE, CAMPAIGNS), (800, 15)])
def test_every_phone_is_match_eligible_under_option_a(people: int, campaigns: int) -> None:
    """The expectations assume every seeded phone can match (ADR 0004 amendment, option A):
    a region-inferred landline would be stored ``inferred`` and never match, splitting the
    phone_formats people and dropping the conflicts. Every format, with or without +91, must
    classify as a mobile."""
    from app.modules.talent.identity import classify_phone

    phones = [s.phone for s in seed.build_plan(people, campaigns, SEED).submissions if s.phone]
    assert phones
    classified = {phone: classify_phone(phone, "IN") for phone in phones}
    assert all(c is not None and c.is_mobile and c.match_eligible for c in classified.values())
    # The formats without a country code are among them (they are the ones option A could drop).
    assert any(p.startswith("0") for p in phones) and any("-" in p for p in phones)


def test_manifest_lists_the_seeded_campaigns() -> None:
    plan = seed.build_plan(PEOPLE, CAMPAIGNS, SEED)
    manifest = seed.build_manifest(plan)
    assert manifest["campaign_ids"] == [str(row["id"]) for row in plan.campaign_rows]
    assert len(set(manifest["campaign_ids"])) == CAMPAIGNS


def test_default_case_sizes() -> None:
    """The quantities documented in the module docstring for ``--people 800``."""
    assert seed.case_sizes(800) == {
        seed.PLAIN: 560,
        seed.EMAIL_CASE: 24,
        seed.GMAIL_DOTS: 24,
        seed.GMAIL_PLUS_TAG: 24,
        seed.OUTLOOK_PLUS_TAG: 8,
        seed.PHONE_FORMATS: 24,
        seed.MULTI_CAMPAIGN: 80,
        seed.SAME_CAMPAIGN_TWICE: 24,
        seed.NO_CONTACT: 24,
        seed.EMAIL_PHONE_CONFLICT: 8,
    }


def test_rejects_sizes_that_cannot_hold_every_case() -> None:
    with pytest.raises(ValueError, match="too small"):
        seed.case_sizes(10)
    with pytest.raises(ValueError, match="at least 3"):
        seed.build_plan(60, 2, SEED)
    assert seed.main(["--people", "10"]) == 1
