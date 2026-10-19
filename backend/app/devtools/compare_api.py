"""Compare two API captures (``app.devtools.capture_api``): the read-switch equivalence check.

Usage::

    python -m app.devtools.compare_api before.json after.json [--show 20]

Exit code ``0`` when every response is identical or differs only in an **expected** way, ``1``
when any other difference exists (each is printed), ``2`` for unreadable input.

Expected differences (E0-19, spec §7), reported with their counts but never failures:

``expected (merged legacy id)``
    The backfill merged several legacy candidates of one person in one campaign into one
    application (``legacy_candidate_map``). After the switch every one of those ids answers
    with that application, under the application's id and with its newest resume's data; the
    lists, exports and stats show it once. Merged ids are found in the *after* capture: a
    ``GET /candidates/{id}`` answering with another ``id``. Every response involving an id of
    such a group is set aside as expected: the group's detail and resume responses, its list
    rows and CSV lines (removed from both sides before the rest is compared exactly), and the
    candidate-derived numbers of its campaign's stats (category counts, average score, top
    skills; the resume counts must still be identical). ``GET /stats``'s ``total_candidates``
    must drop by exactly the number of merged-away ids (plus the unprocessed rows below).

``expected (unprocessed resume)``
    Before the switch the extract step created a placeholder candidate (no name, no score) for
    a resume still being analysed, or one that failed afterwards; after it there is no row until
    a resume is scored. Such rows (resume not ``done`` in the *before* capture) are set aside
    the same way. On a rehearsal with drained uploads there are none.

Everything else must be identical, after the normalisation ``capture_api`` already applied
(the order of equal scores is not part of the contract). The top skills' order among equal
counts is not either: equal-count skills are compared as sets, and for the last, cut-off count
only their number is compared.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path
from typing import Any

# The numbers of a campaign's stats that come from resumes (ingestion), not from candidates:
# they must match even in a campaign with merged candidates.
RESUME_COUNTS = (
    "total_resumes",
    "total_processed",
    "total_failed",
    "total_pending",
    "total_quota_exceeded",
)

IDENTICAL = "identical"  # equal up to what the contract leaves open (tie order)
MERGED = "expected (merged legacy id)"
UNPROCESSED = "expected (unprocessed resume)"


@dataclass
class Comparison:
    identical: int = 0
    expected: dict[str, list[str]] = field(default_factory=lambda: {MERGED: [], UNPROCESSED: []})
    differences: list[str] = field(default_factory=list)
    merged_ids: int = 0

    @property
    def ok(self) -> bool:
        return not self.differences

    def render(self, show: int = 20) -> str:
        total = self.identical + sum(map(len, self.expected.values())) + len(self.differences)
        lines = [f"compare_api: {total} responses compared"]
        lines.append(f"  identical:                         {self.identical}")
        for name, keys in self.expected.items():
            extra = f" ({self.merged_ids} merged legacy ids)" if name == MERGED else ""
            lines.append(f"  {name + ':':<34} {len(keys)}{extra}")
        lines.append(f"  differences:                       {len(self.differences)}")
        lines += [f"  - {d}" for d in self.differences[:show]]
        if len(self.differences) > show:
            lines.append(f"  ... and {len(self.differences) - show} more")
        lines.append(f"compare_api: {'PASSED' if self.ok else 'FAILED'}")
        return "\n".join(lines)


def first_difference(a: Any, b: Any, path: str = "") -> str:
    """Where two JSON values first differ, e.g. ``body[3].score: 72.0 != 71.0``."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                return f"{path}.{k}: only in {'after' if k not in a else 'before'}"
            if a[k] != b[k]:
                return first_difference(a[k], b[k], f"{path}.{k}")
    if isinstance(a, list) and isinstance(b, list):
        for i, (x, y) in enumerate(zip(a, b, strict=False)):
            if x != y:
                return first_difference(x, y, f"{path}[{i}]")
        if len(a) != len(b):
            return f"{path}: {len(a)} items != {len(b)} items"
    return f"{path}: {json.dumps(a)[:120]} != {json.dumps(b)[:120]}"


def _skills_equivalent(a: Sequence[dict[str, Any]], b: Sequence[dict[str, Any]]) -> bool:
    """Top skills equal up to the order of equal counts (and which of the cut-off count made
    the top ten)."""
    if [s["count"] for s in a] != [s["count"] for s in b]:
        return False
    runs_a = [{s["skill"] for s in run} for _, run in groupby(a, key=lambda s: s["count"])]
    runs_b = [{s["skill"] for s in run} for _, run in groupby(b, key=lambda s: s["count"])]
    if len(a) == 10 and runs_a:  # the last run may be cut off: only its size is comparable
        runs_a, runs_b = runs_a[:-1], runs_b[:-1]
    return runs_a == runs_b


def _stats_equivalent(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if {k: v for k, v in a.items() if k != "top_skills"} != {
        k: v for k, v in b.items() if k != "top_skills"
    }:
        return False
    return _skills_equivalent(a.get("top_skills", []), b.get("top_skills", []))


def _csv_key(line: str) -> tuple[str, str]:
    fields = next(csv.reader([line]), [])
    return (fields[0], fields[1]) if len(fields) > 1 else ("", "")


class _Context:
    """What the comparison learns from the two captures before comparing responses."""

    def __init__(self, before: dict[str, Any], after: dict[str, Any]) -> None:
        a, b = before["responses"], after["responses"]
        # Merged legacy ids: asked for one id, answered with another (the application's).
        self.redirect: dict[str, str] = {}
        for key, entry in b.items():
            if key.startswith("detail/") and entry["status"] == 200:
                asked, got = key.removeprefix("detail/"), str(entry["body"]["id"])
                if asked != got:
                    self.redirect[asked] = got
        self.merged: set[str] = set(self.redirect) | set(self.redirect.values())
        before_ids = {k.removeprefix("detail/") for k in a if k.startswith("detail/")}
        groups = Counter(self.redirect.get(i, i) for i in before_ids if i in self.merged)
        self.merged_away = sum(n - 1 for n in groups.values())

        status: dict[str, str] = {}
        for key, entry in a.items():
            if key.startswith("resumes/") and isinstance(entry["body"], list):
                status.update({str(r["id"]): r["status"] for r in entry["body"]})
        self.resume_status = status

        # Per campaign (from the before capture's unfiltered lists): does it hold merged or
        # unprocessed rows, and the (name, email) of those rows (CSV lines have no id).
        self.affected: dict[str, str] = {}
        self.hidden_people: set[tuple[str, str]] = set()
        self.unprocessed_rows = 0
        for key, entry in a.items():
            if not (key.startswith("list/") and key.endswith("/all")):
                continue
            campaign = key.split("/")[1]
            for row in entry["body"] if isinstance(entry["body"], list) else []:
                kind = self.hidden_kind(row)
                if kind is not None:
                    self.affected.setdefault(campaign, kind)
                    self.hidden_people.add((row.get("name") or "", row.get("email") or ""))
                    self.unprocessed_rows += kind == UNPROCESSED
        for detail_id in self.merged:
            for side in (a, b):
                body = side.get(f"detail/{detail_id}", {}).get("body")
                if isinstance(body, dict):
                    self.hidden_people.add((body.get("name") or "", body.get("email") or ""))

    def unprocessed(self, row: Any) -> bool:
        if not isinstance(row, dict):
            return False
        status = self.resume_status.get(str(row.get("resume_id")))
        return status is not None and status != "done"

    def hidden_kind(self, row: Any) -> str | None:
        if isinstance(row, dict) and str(row.get("id")) in self.merged:
            return MERGED
        if self.unprocessed(row):
            return UNPROCESSED
        return None


def compare(before: dict[str, Any], after: dict[str, Any]) -> Comparison:
    """Compare two captures response by response (module docstring)."""
    ctx = _Context(before, after)
    a, b = before["responses"], after["responses"]
    result = Comparison(merged_ids=len(ctx.redirect))
    for key in sorted(set(a) | set(b)):
        if key not in a or key not in b:
            result.differences.append(f"{key}: only in {'after' if key not in a else 'before'}")
            continue
        ea, eb = a[key], b[key]
        if ea == eb:
            result.identical += 1
            continue
        verdict = _judge(ctx, key, ea, eb)
        if verdict == IDENTICAL:
            result.identical += 1
        elif verdict in result.expected:
            result.expected[verdict].append(key)
        else:
            result.differences.append(f"{key}: {verdict}")
    return result


def _judge(ctx: _Context, key: str, ea: dict[str, Any], eb: dict[str, Any]) -> str:
    """``IDENTICAL`` (equal up to tie order), ``MERGED`` / ``UNPROCESSED`` (an expected
    difference), else a description of the difference."""
    kind, _, rest = key.partition("/")
    body_a, body_b = ea.get("body"), eb.get("body")
    if kind in ("detail", "by_resume"):
        ids = {rest} if kind == "detail" else set()
        for body in (body_a, body_b):
            if isinstance(body, dict) and "id" in body:
                ids.add(str(body["id"]))
        if ids & ctx.merged:
            return MERGED
        if ea["status"] == 200 and ctx.unprocessed(body_a) and eb["status"] == 404:
            return UNPROCESSED
        return first_difference(ea, eb)

    if kind == "list" and isinstance(body_a, list) and isinstance(body_b, list):
        if ea["status"] != eb["status"]:
            return first_difference(ea, eb)
        kinds = Counter(ctx.hidden_kind(r) for r in body_a + body_b)
        rows_a = [r for r in body_a if ctx.hidden_kind(r) is None]
        rows_b = [r for r in body_b if ctx.hidden_kind(r) is None]
        if rows_a != rows_b:
            return "without merged/unprocessed rows: " + first_difference(rows_a, rows_b)
        if kinds[MERGED]:
            return MERGED
        return UNPROCESSED if kinds[UNPROCESSED] else IDENTICAL

    if kind == "export" and isinstance(body_a, list) and isinstance(body_b, list):
        if (ea["status"], ea.get("headers")) != (eb["status"], eb.get("headers")):
            return first_difference(ea, eb)
        lines_a = [ln for ln in body_a[1:-1] if _csv_key(ln) not in ctx.hidden_people]
        lines_b = [ln for ln in body_b[1:-1] if _csv_key(ln) not in ctx.hidden_people]
        if [body_a[0], *lines_a] != [body_b[0], *lines_b]:
            return "without merged/unprocessed lines: " + first_difference(lines_a, lines_b)
        return ctx.affected.get(rest.split("/")[0], MERGED)

    if (
        kind in ("campaign_stats", "campaign")
        and isinstance(body_a, dict)
        and isinstance(body_b, dict)
    ):
        nested = kind == "campaign"
        stats_a: dict[str, Any] = (body_a.get("stats") or {}) if nested else body_a
        stats_b: dict[str, Any] = (body_b.get("stats") or {}) if nested else body_b
        rest_a = {k: v for k, v in body_a.items() if k != "stats"} if nested else {}
        rest_b = {k: v for k, v in body_b.items() if k != "stats"} if nested else {}
        if ea["status"] != eb["status"] or rest_a != rest_b:
            return first_difference(ea, eb)
        if _stats_equivalent(stats_a, stats_b):
            return IDENTICAL
        campaign_kind = ctx.affected.get(rest)
        resume_counts_equal = all(stats_a.get(k) == stats_b.get(k) for k in RESUME_COUNTS)
        if campaign_kind is not None and resume_counts_equal:
            return campaign_kind
        return first_difference(stats_a, stats_b)

    if key == "stats" and isinstance(body_a, dict) and isinstance(body_b, dict):
        others_a = {k: v for k, v in body_a.items() if k != "total_candidates"}
        others_b = {k: v for k, v in body_b.items() if k != "total_candidates"}
        if others_a != others_b:
            return first_difference(others_a, others_b)
        drop = body_a["total_candidates"] - body_b["total_candidates"]
        expected_drop = ctx.merged_away + ctx.unprocessed_rows
        if drop == expected_drop:
            return MERGED if ctx.merged_away else UNPROCESSED
        return (
            f"total_candidates dropped by {drop}, expected {expected_drop} "
            f"({ctx.merged_away} merged-away ids + {ctx.unprocessed_rows} unprocessed rows; "
            "capture every campaign for this check)"
        )
    return first_difference(ea, eb)


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if "responses" not in data:
        raise ValueError("not a capture_api file (no 'responses')")
    return data


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.devtools.compare_api",
        description="Compare two capture_api files; exit 1 on any unexpected difference.",
    )
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--show", type=int, default=20, help="differences to print")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        before, after = _load(args.before), _load(args.after)
    except (OSError, ValueError) as exc:
        print(f"compare_api: cannot read the captures: {exc}", file=sys.stderr)
        return 2
    result = compare(before, after)
    print(result.render(show=args.show))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
