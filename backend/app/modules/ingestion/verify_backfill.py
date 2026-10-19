"""Verify the Person/Application backfill (EPIC-00 spec §6.1 phase 3, §6.3): the release gate.

Usage (inside the backend container, or from ``backend/``)::

    python -m app.modules.ingestion.verify_backfill [--manifest path/to/seed-manifest.json]

Exits ``0`` when every check passes, ``1`` when any fails (``2`` for a bad command line or an
unreadable manifest), and prints a readable report either way. Read-only: it never writes.

Checks (spec §6.3, plus the invariants the Sprint 4 read switch relies on)
    * ``candidates_lead_to_one_application``: every legacy candidate whose resume is ``done``
      is an application's id **or** has a ``legacy_candidate_map`` row, never both and never
      neither; the application is in the candidate's campaign and belongs to the Person of the
      candidate's resume. Candidates whose resume is not ``done`` are counted, not failed.
    * ``applications_have_people``: every ``applications.person_id`` exists in ``people``.
    * ``applications_have_resumes``: every ``applications.resume_id`` exists in ``resumes``,
      in the same campaign, resolved to the same Person.
    * ``done_resumes_have_people``: every ``done`` resume has a ``person_id`` that exists in
      ``people`` and agrees with talent's record of the resolution (``person_resumes``).
    * ``identifiers_unique``: no ``(kind, value_normalized)`` stored twice.
    * ``people_without_identifiers_justified``: a Person with no identifier is allowed only when
      none of its (remaining) resumes had a match-eligible one (ADR 0004 amendment, option A: an
      inferred phone does not count as usable).
    * ``people_have_resumes``: every Person was resolved from at least one resume.
    * ``applications_have_extractions`` (E0-19): every application's current resume has an
      extraction snapshot (``resumes.extracted``), which the legacy candidate endpoints show as
      the candidate's name, contacts and profile.
    * With ``--manifest``: ``expected.{people, applications, identity_conflicts,
      legacy_candidate_map_rows}`` of the seed manifest (``app.devtools.seed``), exactly, and
      the number of legacy candidates (``counts.legacy_candidates``). When the manifest lists its
      ``campaign_ids``, the comparison counts only rows in those campaigns, so uploads to other
      campaigns during a rehearsal don't change it; the integrity checks always cover everything.

After the read switch (E0-19)
    The API serves ``applications``; ``candidates`` is frozen (nothing writes it, E0-20 drops
    it). The checks stay meaningful on a switched database:

    * candidates created before the switch still each lead to one application (a legacy delete
      now removes the application with its resumes, and the candidates go with the resumes
      through the foreign key, so none is left dangling);
    * applications created after the switch have no candidate and are not expected to: they
      are counted (``without a legacy candidate (new)``) and covered by every application
      check;
    * ``recruiter_fields_consistent`` (E0-18) is gone: after the switch the recruiter edits the
      application itself, so it no longer equals the rule over its frozen candidates. It was a
      pre-switch gate, run by the E0-18 code (runbook §8);
    * ``people_without_identifiers_justified`` skips resumes that were deleted.

Cross-module reads go through each module's service (``hiring.service``, ``talent.service``);
ingestion reads its own ``resumes``.
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
from app.modules.ingestion import repository, tasks
from app.modules.talent import service as talent_service
from app.platform.config import settings

MANIFEST_KEYS = ("people", "applications", "identity_conflicts", "legacy_candidate_map_rows")
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
        lines = ["verify_backfill: counts"]
        lines += [f"  {name:<34} {value}" for name, value in self.counts.items()]
        lines.append("verify_backfill: checks")
        for check in self.checks:
            if check.ok:
                lines.append(f"  PASS {check.name}")
                continue
            lines.append(f"  FAIL {check.name}: {len(check.problems)} problem(s)")
            lines += [f"       - {p}" for p in check.problems[:MAX_EXAMPLES]]
            if len(check.problems) > MAX_EXAMPLES:
                lines.append(f"       ... and {len(check.problems) - MAX_EXAMPLES} more")
        if self.manifest:
            lines.append(f"verify_backfill: manifest ({self.manifest_scope})")
            for key, expected, actual in self.manifest:
                verdict = "PASS" if expected == actual else "FAIL"
                lines.append(f"  {verdict} {key:<28} expected {expected:>7}  actual {actual:>7}")
        lines.append(f"verify_backfill: {'PASSED' if self.ok else 'FAILED'}")
        return "\n".join(lines)


def verify(
    db: Session, *, manifest: dict[str, Any] | None = None, default_region: str | None = None
) -> Report:
    region = default_region or settings.PHONE_DEFAULT_REGION
    report = Report()

    candidates = hiring_service.legacy_candidate_refs(db)
    applications = {a.id: a for a in hiring_service.application_refs(db)}
    legacy_map = hiring_service.legacy_candidate_map(db)
    resumes = {row.id: row for row in repository.resume_refs_sync(db)}
    people = talent_service.person_ids(db)
    resolutions = talent_service.resolutions(db)
    talent_counts = talent_service.counts(db)
    conflict_resumes = talent_service.identity_conflict_resume_ids(db)

    done = {rid for rid, row in resumes.items() if row.status == "done"}
    backfillable = [c for c in candidates if c.resume_id in done]
    with_legacy = {c.id for c in candidates} | set(legacy_map.values())
    report.counts = {
        "legacy candidates": len(candidates),
        "  with a done resume": len(backfillable),
        "  with a resume not done (skipped)": len(candidates) - len(backfillable),
        "resumes": len(resumes),
        "  done": len(done),
        "people": talent_counts.people,
        "person identifiers": talent_counts.identifiers,
        "identity conflicts": talent_counts.identity_conflicts,
        "resolved resumes (person_resumes)": talent_counts.resolved_resumes,
        "applications": len(applications),
        "  without a legacy candidate (new)": sum(1 for a in applications if a not in with_legacy),
        "legacy_candidate_map rows": len(legacy_map),
    }

    # Every done candidate leads to exactly one application, consistent with its resume.
    check = Check("candidates_lead_to_one_application")
    for c in backfillable:
        direct, redirected = c.id in applications, c.id in legacy_map
        if direct and redirected:
            check.fail(f"candidate {c.id} is an application id and also in legacy_candidate_map")
            continue
        if not direct and not redirected:
            check.fail(f"candidate {c.id} leads to no application")
            continue
        app = applications.get(c.id if direct else legacy_map[c.id])
        if app is None:
            check.fail(f"candidate {c.id} is redirected to a missing application")
        elif app.campaign_id != c.campaign_id:
            check.fail(f"candidate {c.id} leads to application {app.id} of another campaign")
        elif app.person_id != resumes[c.resume_id].person_id:
            check.fail(
                f"candidate {c.id} leads to application {app.id} of person {app.person_id}, "
                f"but its resume belongs to {resumes[c.resume_id].person_id}"
            )
    report.checks.append(check)

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
    for kind, value, rows in talent_service.duplicate_identifiers(db):
        check.fail(f"{kind} {value!r} is stored {rows} times")
    report.checks.append(check)

    check = Check("people_without_identifiers_justified")
    bare = talent_service.people_without_identifiers(db)
    extracted = repository.extracted_sync(db, [r for rids in bare.values() for r in rids])
    for person_id, resume_ids in bare.items():
        for rid in resume_ids:
            if rid not in resumes:
                continue  # deleted with its candidate or application: nothing left to check
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

    # The read path shows the extraction of an application's current resume (E0-19, spec §7).
    check = Check("applications_have_extractions")
    snapshots = repository.extracted_sync(db, [a.resume_id for a in applications.values()])
    for app in applications.values():
        if app.resume_id in snapshots and snapshots[app.resume_id] is None:
            check.fail(f"application {app.id}: resume {app.resume_id} has no extraction")
    report.checks.append(check)

    if manifest is not None:
        _compare_manifest(
            report, manifest, candidates, applications, legacy_map, resumes, conflict_resumes
        )
    return report


def _compare_manifest(
    report: Report,
    manifest: dict[str, Any],
    candidates: Sequence[hiring_service.LegacyCandidateRef],
    applications: dict[uuid.UUID, hiring_service.ApplicationRef],
    legacy_map: dict[uuid.UUID, uuid.UUID],
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

    apps = {a.id: a for a in applications.values() if in_scope(a.campaign_id)}
    conflicts = Counter(
        rid for rid in conflict_resumes if rid in resumes and in_scope(resumes[rid].campaign_id)
    )
    actual = {
        "people": len({a.person_id for a in apps.values()}),
        "applications": len(apps),
        "identity_conflicts": sum(conflicts.values()),
        "legacy_candidate_map_rows": sum(1 for app_id in legacy_map.values() if app_id in apps),
    }
    expected = manifest.get("expected", {})
    report.manifest = [
        (f"expected.{key}", int(expected[key]), actual[key])
        for key in MANIFEST_KEYS
        if key in expected
    ]
    if "legacy_candidates" in manifest.get("counts", {}):
        report.manifest.append(
            (
                "counts.legacy_candidates",
                int(manifest["counts"]["legacy_candidates"]),
                sum(1 for c in candidates if in_scope(c.campaign_id)),
            )
        )


# ── CLI ───────────────────────────────────────────────────────
def main(
    argv: Sequence[str] | None = None, session_factory: Callable[[], Session] | None = None
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.modules.ingestion.verify_backfill",
        description="Verify the Person/Application backfill (spec §6.3). Read-only.",
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
            print(f"verify_backfill: cannot read manifest {args.manifest}: {exc}", file=sys.stderr)
            return 2

    with (session_factory or tasks.SyncSession)() as db:
        # One snapshot for every read: uploads committed while it runs can't make two reads
        # disagree (e.g. an application whose resume "does not exist" yet).
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        report = verify(db, manifest=manifest)
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
