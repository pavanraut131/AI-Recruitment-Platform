"""E0-12: the seed generator inserts what its manifest says, deterministically, with every §6.4 case.

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
from app.models import Campaign, Candidate, Resume
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


# ── Against the database ─────────────────────────────────────
async def test_row_counts_match_the_manifest(db_session: AsyncSession) -> None:
    manifest = await run_seed(db_session)

    counts = manifest["counts"]
    assert await count(db_session, Campaign) == counts["campaigns"] == CAMPAIGNS
    assert await count(db_session, Resume) == counts["resumes"]
    assert await count(db_session, Candidate) == counts["legacy_candidates"] == counts["resumes"]
    assert counts["true_people"] == PEOPLE
    done = await db_session.scalar(
        select(func.count()).select_from(Resume).where(Resume.status == "done")
    )
    assert done == counts["resumes"]
    no_contact = await db_session.scalar(
        select(func.count())
        .select_from(Candidate)
        .where(Candidate.email.is_(None), Candidate.phone.is_(None))
    )
    assert no_contact == manifest["cases"][seed.NO_CONTACT]["candidates"]


async def test_the_database_run_matches_the_pure_plan(db_session: AsyncSession) -> None:
    """Same seed, same manifest: whether built in memory or inserted into the database."""
    manifest = await run_seed(db_session)
    assert json.dumps(manifest, sort_keys=True) == json.dumps(manifest_for(), sort_keys=True)


async def test_every_spec_case_is_in_the_database(db_session: AsyncSession) -> None:
    manifest = await run_seed(db_session)
    cases = manifest["cases"]
    assert set(cases) == set(seed.ALL_CASES)

    found: dict[str, list[Candidate]] = {}
    for name, case in cases.items():
        assert case["true_people"] >= 1 and case["candidates"] >= 1, name
        rows = []
        for example in case["examples"]:
            row = await db_session.get(Candidate, uuid.UUID(example["candidate_id"]))
            assert row is not None, (name, example)
            assert str(row.campaign_id) == example["campaign_id"]
            assert (row.email, row.phone) == (example["email"], example["phone"])
            rows.append(row)
        found[name] = rows

    def emails(name: str) -> list[str]:
        return [r.email for r in found[name] if r.email is not None]

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
    phones = sorted(r.phone for r in found[seed.PHONE_FORMATS] if r.phone)
    assert len({digits(p) for p in phones}) == 1
    national = digits(phones[0])
    assert phones == sorted(
        [f"+91 {national[:5]} {national[5:]}", f"0{national}", f"{national[:5]}-{national[5:]}"]
    )
    assert all(r.email is None for r in found[seed.PHONE_FORMATS])
    # Several campaigns; twice in one campaign.
    assert len({r.campaign_id for r in found[seed.MULTI_CAMPAIGN]}) >= 2
    twice = found[seed.SAME_CAMPAIGN_TWICE]
    assert len(twice) == 2 and twice[0].campaign_id == twice[1].campaign_id
    assert twice[0].resume_id != twice[1].resume_id
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
    # The conflict resume comes after A's and B's own (the backfill orders by created_at).
    by_id = {str(r.id): r for r in found[seed.EMAIL_PHONE_CONFLICT]}
    conflict_at = by_id[conflict["candidate_id"]].created_at
    assert by_id[owner["candidate_id"]].created_at < conflict_at
    assert by_id[other["candidate_id"]].created_at < conflict_at
    # No contact details at all.
    assert all(r.email is None and r.phone is None for r in found[seed.NO_CONTACT])


async def test_refuses_when_candidates_exist(db_session: AsyncSession) -> None:
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
    assert await count(db_session, Candidate) == 0


async def test_reset_deletes_only_the_seeded_tables_and_reports_counts(
    db_session: AsyncSession,
) -> None:
    manifest = await run_seed(db_session)
    deleted = await db_session.run_sync(seed.reset)
    assert deleted == {
        "campaigns": manifest["counts"]["campaigns"],
        "resumes": manifest["counts"]["resumes"],
        "candidates": manifest["counts"]["legacy_candidates"],
    }
    for model in (Campaign, Resume, Candidate):
        assert await count(db_session, model) == 0
    # After a reset the generator runs again.
    assert await run_seed(db_session) == manifest


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
    # Cases partition the people and the candidates, and their expectations add up.
    assert sum(c["true_people"] for c in cases.values()) == counts["true_people"] == people
    assert sum(c["candidates"] for c in cases.values()) == counts["legacy_candidates"]
    for key in ("people", "applications", "identity_conflicts"):
        assert sum(c[f"expected_{key}"] for c in cases.values()) == expected[key], key
    assert expected["legacy_candidate_map_rows"] == (
        counts["legacy_candidates"] - expected["applications"]
    )

    # The documented derivation (module docstring of app.devtools.seed).
    no_contact = cases[seed.NO_CONTACT]
    assert expected["people"] == people - no_contact["true_people"] + no_contact["candidates"]
    twice = cases[seed.SAME_CAMPAIGN_TWICE]
    assert twice["expected_applications"] == twice["true_people"] == twice["candidates"] // 2
    assert expected["applications"] == counts["legacy_candidates"] - twice["true_people"]
    conflicts = cases[seed.EMAIL_PHONE_CONFLICT]
    assert expected["identity_conflicts"] == conflicts["true_people"] // 2
    assert conflicts["candidates"] == 3 * (conflicts["true_people"] // 2)
    for name in seed.ALL_CASES:
        case = cases[name]
        if name not in (seed.NO_CONTACT, seed.SAME_CAMPAIGN_TWICE):
            assert case["expected_people"] == case["true_people"], name
            assert case["expected_applications"] == case["candidates"], name
        if name != seed.EMAIL_PHONE_CONFLICT:
            assert case["expected_identity_conflicts"] == 0, name
    assert cases[seed.EMAIL_CASE]["candidates"] == 2 * cases[seed.EMAIL_CASE]["true_people"]
    assert cases[seed.PHONE_FORMATS]["candidates"] == 3 * cases[seed.PHONE_FORMATS]["true_people"]
    multi = cases[seed.MULTI_CAMPAIGN]
    assert multi["candidates"] >= 2 * multi["true_people"]


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
