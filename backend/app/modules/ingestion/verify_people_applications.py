"""Verify the consistency of People, Applications and resumes across modules (EPIC-00 spec §6.3).

Usage (inside the backend container, or from ``backend/``)::

    python -m app.modules.ingestion.verify_people_applications [--manifest seed-manifest.json]

Exits ``0`` when every check passes, ``1`` when any fails (``2`` for a bad command line or an
unreadable manifest), and prints a readable report either way. Read-only: it never writes.

Why it exists after the migration: the modules' tables refer to each other by plain ids, with
no foreign keys (ADR 0003), so nothing in the database keeps them consistent; the services do,
and this command checks that they did. It replaced ``verify_backfill`` in the contract phase
(E0-20): the checks that compared the legacy ``candidates`` with the new model went with that
table, the rest are unchanged.

Checks
    * ``applications_have_people``: every ``applications.person_id`` exists in ``people``.
    * ``applications_have_resumes``: every ``applications.resume_id`` exists in ``resumes``,
      in the same campaign, resolved to the same Person.
    * ``done_resumes_have_people``: every ``done`` resume has a ``person_id`` that exists in
      ``people`` and agrees with talent's record of the resolution (``person_resumes``).
    * ``identifiers_unique``: no ``(agency_id, kind, value_normalized)`` stored twice (the same
      value in two agencies is two People, by design: EPIC-02 spec §3.4).
    * ``people_without_identifiers_justified``: a Person with no identifier is allowed only when
      none of its (remaining) resumes had a match-eligible one (ADR 0004 amendment, option A: an
      inferred phone does not count as usable).
    * ``people_have_resumes``: every Person was resolved from at least one resume.
    * ``applications_have_extractions``: every application's current resume has an extraction
      snapshot (``resumes.extracted``), which the application resources show as the person's
      name, contacts and profile (spec §7.1).
    * ``tenancy_complete`` (EPIC-02 spec §7.3): no row of a tenant table (``campaigns``,
      ``applications``, ``people``, ``person_identifiers``, ``identity_conflicts``,
      ``person_resumes``, ``resumes``) is without an ``agency_id``, and every ``agency_id`` is a
      known agency. Fails until the tenancy backfill (``backfill_tenancy``) has run.
    * ``cross_module_same_agency``: an application has the agency of its campaign, its Person
      and its resume; a resume has the agency of its campaign and of its Person. Pairs where
      either side has no agency yet are left to ``tenancy_complete``, and missing rows to the
      checks above.
    * ``clients_same_agency``: every campaign has a client, the client exists, and it belongs to
      the campaign's agency (the contract's composite foreign key enforces the same).
    * With ``--manifest``: ``expected.{people, applications, identity_conflicts}`` of the seed
      manifest (``app.devtools.seed``), exactly. When the manifest lists its ``campaign_ids``,
      the comparison counts only rows in those campaigns, so uploads to other campaigns during
      a rehearsal don't change it; the integrity checks always cover everything.

Until E2-12 the report also counted the rows of ``legacy_candidate_map`` (the E0 backfill's
historical id map), dropped with ``/api/v1``.

Cross-module reads go through each module's service (``hiring.service``, ``talent.service``,
``identity.service``); ingestion reads its own ``resumes``.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.modules.hiring import service as hiring_service
from app.modules.identity import service as identity_service
from app.modules.ingestion import repository
from app.modules.talent import service as talent_service
from app.platform import maintenance
from app.platform.config import settings

PROG = "verify_people_applications"
MANIFEST_KEYS = ("people", "applications", "identity_conflicts")
MAX_EXAMPLES = 10


@dataclass
class Check:
    name: str
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def fail(self, message: str) -> None:
        self.problems.append(message)


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    # (key, expected, actual) of the manifest comparison; scope describes what was counted.
    manifest: list[tuple[str, int, int]] = field(default_factory=list)
    manifest_scope: str | None = None

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks) and all(
            expected == actual for _, expected, actual in self.manifest
        )

    def render(self) -> str:
        lines = [f"{PROG}: counts"]
        lines += [f"  {name:<34} {value}" for name, value in self.counts.items()]
        lines.append(f"{PROG}: checks")
        for check in self.checks:
            if check.ok:
                lines.append(f"  PASS {check.name}")
                continue
            lines.append(f"  FAIL {check.name}: {len(check.problems)} problem(s)")
            lines += [f"       - {p}" for p in check.problems[:MAX_EXAMPLES]]
            if len(check.problems) > MAX_EXAMPLES:
                lines.append(f"       ... and {len(check.problems) - MAX_EXAMPLES} more")
        if self.manifest:
            lines.append(f"{PROG}: manifest ({self.manifest_scope})")
            for key, expected, actual in self.manifest:
                verdict = "PASS" if expected == actual else "FAIL"
                lines.append(f"  {verdict} {key:<28} expected {expected:>7}  actual {actual:>7}")
        lines.append(f"{PROG}: {'PASSED' if self.ok else 'FAILED'}")
        return "\n".join(lines)


def verify(
    db: Session, *, manifest: dict[str, Any] | None = None, default_region: str | None = None
) -> Report:
    region = default_region or settings.PHONE_DEFAULT_REGION
    report = Report()

    applications = {a.id: a for a in hiring_service.application_refs(db)}
    resumes = {row.id: row for row in repository.resume_refs_sync(db)}
    people = talent_service.person_ids(db)
    resolutions = talent_service.resolutions(db)
    talent_counts = talent_service.counts(db)
    conflict_resumes = talent_service.identity_conflict_resume_ids(db)

    done = {rid for rid, row in resumes.items() if row.status == "done"}
    report.counts = {
        "resumes": len(resumes),
        "  done": len(done),
        "people": talent_counts.people,
        "person identifiers": talent_counts.identifiers,
        "identity conflicts": talent_counts.identity_conflicts,
        "resolved resumes (person_resumes)": talent_counts.resolved_resumes,
        "applications": len(applications),
    }

    check = Check("applications_have_people")
    for app in applications.values():
        if app.person_id not in people:
            check.fail(f"application {app.id}: person {app.person_id} does not exist")
    report.checks.append(check)

    check = Check("applications_have_resumes")
    for app in applications.values():
        resume = resumes.get(app.resume_id)
        if resume is None:
            check.fail(f"application {app.id}: resume {app.resume_id} does not exist")
        elif resume.campaign_id != app.campaign_id:
            check.fail(f"application {app.id}: resume {app.resume_id} is in another campaign")
        elif resume.person_id != app.person_id:
            check.fail(f"application {app.id}: resume {app.resume_id} belongs to another person")
    report.checks.append(check)

    check = Check("done_resumes_have_people")
    for rid in sorted(done):
        person_id = resumes[rid].person_id
        if person_id is None:
            check.fail(f"resume {rid} is done but has no person_id")
        elif person_id not in people:
            check.fail(f"resume {rid}: person {person_id} does not exist")
        elif resolutions.get(rid) != person_id:
            check.fail(
                f"resume {rid}: person_id {person_id} but talent resolved it to "
                f"{resolutions.get(rid)}"
            )
    report.checks.append(check)

    check = Check("identifiers_unique")
    for agency_id, kind, value, rows in talent_service.duplicate_identifiers(db):
        check.fail(f"{kind} {value!r} is stored {rows} times in agency {agency_id}")
    report.checks.append(check)

    check = Check("people_without_identifiers_justified")
    bare = talent_service.people_without_identifiers(db)
    extracted = repository.extracted_sync(db, [r for rids in bare.values() for r in rids])
    for person_id, resume_ids in bare.items():
        for rid in resume_ids:
            if rid not in resumes:
                continue  # deleted with its application: nothing left to check
            snapshot = extracted.get(rid)
            if snapshot is None:
                check.fail(f"person {person_id}: resume {rid} has no extraction to check")
            elif talent_service.has_match_eligible_identifier(
                email=snapshot.get("email"), phone=snapshot.get("phone"), default_region=region
            ):
                check.fail(
                    f"person {person_id} has no identifiers, but resume {rid} has a usable one"
                )
    report.checks.append(check)

    check = Check("people_have_resumes")
    resolved_people = set(resolutions.values())
    for person_id in sorted(people - resolved_people):
        check.fail(f"person {person_id} was resolved from no resume")
    report.checks.append(check)

    # The read path shows the extraction of an application's current resume (spec §7.1).
    check = Check("applications_have_extractions")
    snapshots = repository.extracted_sync(db, [a.resume_id for a in applications.values()])
    for app in applications.values():
        if app.resume_id in snapshots and snapshots[app.resume_id] is None:
            check.fail(f"application {app.id}: resume {app.resume_id} has no extraction")
    report.checks.append(check)

    _check_tenancy(db, report, applications, resumes)

    if manifest is not None:
        _compare_manifest(report, manifest, applications, resumes, conflict_resumes)
    return report


def _check_tenancy(
    db: Session,
    report: Report,
    applications: dict[uuid.UUID, hiring_service.ApplicationRef],
    resumes: dict[uuid.UUID, Any],
) -> None:
    """The EPIC-02 checks (spec §7.3)."""
    agencies = identity_service.agency_ids(db)
    campaigns = hiring_service.campaign_tenancy(db)
    people = talent_service.person_agencies(db)
    census = {
        **hiring_service.agency_census(db),
        **talent_service.agency_census(db),
        "resumes": repository.agency_census_sync(db),
    }
    report.counts["agencies"] = len(agencies)

    check = Check("tenancy_complete")
    for table, rows_by_agency in census.items():
        for agency_id, rows in sorted(rows_by_agency.items(), key=lambda kv: str(kv[0])):
            if agency_id is None:
                check.fail(f"{table}: {rows} row(s) without an agency (run backfill_tenancy)")
            elif agency_id not in agencies:
                check.fail(f"{table}: {rows} row(s) of unknown agency {agency_id}")
    report.checks.append(check)

    def differ(a: uuid.UUID | None, b: uuid.UUID | None) -> bool:
        return a is not None and b is not None and a != b

    check = Check("cross_module_same_agency")
    for app in applications.values():
        campaign = campaigns.get(app.campaign_id)
        resume = resumes.get(app.resume_id)
        if campaign is not None and differ(app.agency_id, campaign.agency_id):
            check.fail(
                f"application {app.id} (agency {app.agency_id}): campaign {app.campaign_id} "
                f"is in agency {campaign.agency_id}"
            )
        if differ(app.agency_id, people.get(app.person_id)):
            check.fail(
                f"application {app.id} (agency {app.agency_id}): person {app.person_id} is in "
                f"agency {people[app.person_id]}"
            )
        if resume is not None and differ(app.agency_id, resume.agency_id):
            check.fail(
                f"application {app.id} (agency {app.agency_id}): resume {app.resume_id} is in "
                f"agency {resume.agency_id}"
            )
    for rid, resume in resumes.items():
        campaign = campaigns.get(resume.campaign_id)
        if campaign is not None and differ(resume.agency_id, campaign.agency_id):
            check.fail(
                f"resume {rid} (agency {resume.agency_id}): campaign {resume.campaign_id} is "
                f"in agency {campaign.agency_id}"
            )
        if resume.person_id is not None and differ(resume.agency_id, people.get(resume.person_id)):
            check.fail(
                f"resume {rid} (agency {resume.agency_id}): person {resume.person_id} is in "
                f"agency {people[resume.person_id]}"
            )
    report.checks.append(check)

    check = Check("clients_same_agency")
    clients = hiring_service.client_agencies(db)
    for campaign_id, campaign in campaigns.items():
        if campaign.client_id is None:
            check.fail(f"campaign {campaign_id} has no client")
        elif campaign.client_id not in clients:
            check.fail(f"campaign {campaign_id}: client {campaign.client_id} does not exist")
        elif differ(campaign.agency_id, clients[campaign.client_id]):
            check.fail(
                f"campaign {campaign_id} (agency {campaign.agency_id}): client "
                f"{campaign.client_id} is in agency {clients[campaign.client_id]}"
            )
    report.checks.append(check)


def _compare_manifest(
    report: Report,
    manifest: dict[str, Any],
    applications: dict[uuid.UUID, hiring_service.ApplicationRef],
    resumes: dict[uuid.UUID, Any],
    conflict_resumes: Iterable[uuid.UUID],
) -> None:
    scope_ids = manifest.get("campaign_ids")
    in_scope: Callable[[uuid.UUID], bool]
    if scope_ids:
        campaigns = {uuid.UUID(str(c)) for c in scope_ids}
        report.manifest_scope = f"rows in the manifest's {len(campaigns)} campaigns"

        def in_scope(campaign_id: uuid.UUID) -> bool:
            return campaign_id in campaigns
    else:
        report.manifest_scope = "whole database: the manifest lists no campaign_ids"

        def in_scope(campaign_id: uuid.UUID) -> bool:
            return True

    apps = [a for a in applications.values() if in_scope(a.campaign_id)]
    conflicts = Counter(
        rid for rid in conflict_resumes if rid in resumes and in_scope(resumes[rid].campaign_id)
    )
    actual = {
        "people": len({a.person_id for a in apps}),
        "applications": len(apps),
        "identity_conflicts": sum(conflicts.values()),
    }
    expected = manifest.get("expected", {})
    report.manifest = [
        (f"expected.{key}", int(expected[key]), actual[key])
        for key in MANIFEST_KEYS
        if key in expected
    ]


# ── CLI ───────────────────────────────────────────────────────
def main(
    argv: Sequence[str] | None = None, session_factory: Callable[[], Session] | None = None
) -> int:
    parser = argparse.ArgumentParser(
        prog=f"python -m app.modules.ingestion.{PROG}",
        description="Verify People, Applications and resumes across modules (spec §6.3). "
        "Read-only.",
    )
    parser.add_argument(
        "--manifest", type=Path, default=None, help="seed manifest to compare against exactly"
    )
    args = parser.parse_args(argv)

    manifest: dict[str, Any] | None = None
    if args.manifest is not None:
        try:
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"{PROG}: cannot read manifest {args.manifest}: {exc}", file=sys.stderr)
            return 2

    # The owner role (spec §6.2): every agency's rows; the application role would see one.
    with (session_factory or maintenance.owner_session)() as db:
        # One snapshot for every read: uploads committed while it runs can't make two reads
        # disagree (e.g. an application whose resume "does not exist" yet).
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        report = verify(db, manifest=manifest)
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
