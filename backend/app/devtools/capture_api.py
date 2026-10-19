"""Record what every legacy ``GET`` endpoint returns, for the read-switch equivalence check.

Usage (from ``backend/``, or inside the backend container)::

    python -m app.devtools.capture_api --base-url http://localhost:8000 --out before.json
        [--campaigns all | N | <id>,<id>,...] [--ids-from before.json]

Then compare two captures with ``python -m app.devtools.compare_api before.json after.json``.
The E0-19 live rehearsal (runbook §8): capture on the E0-18 code, deploy E0-19, capture again
with ``--ids-from before.json``, compare. The same around any later deploy that must not change
the API, e.g. the E0-20 contract (runbook §9), where every response must be identical.

What it requests (all under ``/api/v1``)
    * ``GET /campaigns`` (every campaign), and per selected campaign ``GET /campaigns/{id}``,
      ``GET /campaigns/{id}/stats`` and ``GET /campaigns/{id}/resumes``;
    * ``GET /stats``;
    * per campaign, the candidate list for each of :data:`LIST_FILTERS`, every page
      (``page_size=100``) concatenated, and the CSV export for each of :data:`EXPORT_FILTERS`;
    * ``GET /candidates/{id}`` for every id seen in a list, plus every candidate id of
      ``--ids-from`` (a previous capture): after the switch the ids of merged legacy
      candidates appear in no list, but they must still answer;
    * ``GET /resumes/{id}/candidate`` for every resume of the selected campaigns.

The capture (JSON) maps a key per request (``"list/<campaign>/<filter>"``, ``"detail/<id>"``,
...) to ``{"status": ..., "body": ...}``; CSV bodies are kept as their lines, and the export's
content type and disposition headers are recorded. It is normalised only where the API's own
order is undefined: rows with equal scores in a list (and equal score text in a CSV) are sorted
by id (by line), resumes by id. Nothing else is touched: the comparison is exact.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path
from typing import Any

import httpx

API = "/api/v1"
PAGE_SIZE = 100
CAPTURE_VERSION = 1

# Filter combinations for the list: every filter of the endpoint at least once.
LIST_FILTERS: dict[str, dict[str, Any]] = {
    "all": {},
    "category_strong": {"category": "strong_match"},
    "score_50_80": {"min_score": 50, "max_score": 80},
    "experience_3": {"min_experience": 3},
    "skill_python": {"skill": "python"},
    "search_a": {"search": "a"},
    "search_example": {"search": "example"},
    "stage_screened": {"pipeline_stage": "screened"},
    "combined": {"category": "moderate_match", "min_score": 30, "skill": "s", "search": "e"},
}
# The export accepts no skill / min_experience (B8, recorded): not tried.
EXPORT_FILTERS: dict[str, dict[str, Any]] = {
    "all": {},
    "category_strong": {"category": "strong_match"},
    "search_a": {"search": "a"},
}


def _entry(resp: httpx.Response) -> dict[str, Any]:
    try:
        body: Any = resp.json()
    except ValueError:
        body = resp.text
    return {"status": resp.status_code, "body": body}


def _score_key(row: dict[str, Any]) -> tuple[bool, float]:
    score = row.get("score")
    return (score is None, -(score or 0.0))


def normalise_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows of equal score sorted by id: their relative order is not part of the contract
    (the legacy query ordered by score only, so Postgres returned ties in any order)."""
    out: list[dict[str, Any]] = []
    for _, run in groupby(rows, key=_score_key):
        out += sorted(run, key=lambda r: str(r.get("id")))
    return out


def normalise_csv(text: str) -> list[str]:
    """The CSV's lines, with data lines of equal ``Score`` text sorted (same reason)."""
    lines = text.split("\r\n")
    if len(lines) < 2:
        return lines
    header, body, tail = lines[0], lines[1:-1], lines[-1:]
    try:
        column = header.split(",").index("Score")
    except ValueError:
        return lines

    def score_of(line: str) -> str:
        fields = next(csv.reader([line]), [])
        return fields[column] if column < len(fields) else ""

    out = [header]
    for _, run in groupby(body, key=score_of):
        out += sorted(run)
    return out + tail


async def _all_pages(
    client: httpx.AsyncClient, campaign_id: str, params: dict[str, Any]
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        resp = await client.get(
            f"{API}/campaigns/{campaign_id}/candidates",
            params={**params, "page": page, "page_size": PAGE_SIZE},
        )
        if resp.status_code != 200:
            return _entry(resp)
        batch = resp.json()
        rows += batch
        if len(batch) < PAGE_SIZE:
            return {"status": 200, "body": normalise_rows(rows)}
        page += 1


def _select(campaigns: list[dict[str, Any]], which: str) -> list[str]:
    ids = [str(c["id"]) for c in campaigns]
    if which == "all":
        return ids
    if which.isdigit():
        return ids[: int(which)]
    wanted = [w.strip() for w in which.split(",") if w.strip()]
    unknown = sorted(set(wanted) - set(ids))
    if unknown:
        raise SystemExit(f"capture_api: unknown campaign id(s): {', '.join(unknown)}")
    return wanted


def candidate_ids_of(capture: dict[str, Any]) -> list[str]:
    """Every candidate id a capture saw: in its lists and its detail requests."""
    ids: set[str] = set()
    for key, entry in capture.get("responses", {}).items():
        if key.startswith("detail/"):
            ids.add(key.removeprefix("detail/"))
        elif key.startswith("list/") and isinstance(entry.get("body"), list):
            ids.update(str(row["id"]) for row in entry["body"])
    return sorted(ids)


async def capture(
    client: httpx.AsyncClient,
    *,
    campaigns: str = "all",
    extra_candidate_ids: Sequence[str] = (),
    progress: Any = print,
) -> dict[str, Any]:
    """Request every legacy ``GET`` endpoint (module docstring) and return the capture."""
    responses: dict[str, Any] = {}
    resp = await client.get(f"{API}/campaigns", params={"skip": 0, "limit": 100_000})
    resp.raise_for_status()
    responses["campaigns"] = _entry(resp)
    selected = _select(resp.json(), campaigns)
    responses["stats"] = _entry(await client.get(f"{API}/stats"))

    candidate_ids: set[str] = set(extra_candidate_ids)
    resume_ids: list[str] = []
    for n, campaign_id in enumerate(selected, 1):
        responses[f"campaign/{campaign_id}"] = _entry(
            await client.get(f"{API}/campaigns/{campaign_id}")
        )
        responses[f"campaign_stats/{campaign_id}"] = _entry(
            await client.get(f"{API}/campaigns/{campaign_id}/stats")
        )
        resumes = _entry(await client.get(f"{API}/campaigns/{campaign_id}/resumes"))
        if isinstance(resumes["body"], list):
            resumes["body"] = sorted(resumes["body"], key=lambda r: str(r["id"]))
            resume_ids += [str(r["id"]) for r in resumes["body"]]
        responses[f"resumes/{campaign_id}"] = resumes
        for name, params in LIST_FILTERS.items():
            entry = await _all_pages(client, campaign_id, params)
            responses[f"list/{campaign_id}/{name}"] = entry
            if isinstance(entry["body"], list):
                candidate_ids.update(str(row["id"]) for row in entry["body"])
        for name, params in EXPORT_FILTERS.items():
            resp = await client.get(
                f"{API}/campaigns/{campaign_id}/candidates/export", params=params
            )
            responses[f"export/{campaign_id}/{name}"] = {
                "status": resp.status_code,
                "headers": {
                    h: resp.headers.get(h) for h in ("content-type", "content-disposition")
                },
                "body": normalise_csv(resp.text),
            }
        progress(f"capture_api: campaign {n}/{len(selected)} {campaign_id}")

    for candidate_id in sorted(candidate_ids):
        responses[f"detail/{candidate_id}"] = _entry(
            await client.get(f"{API}/candidates/{candidate_id}")
        )
    for resume_id in sorted(resume_ids):
        responses[f"by_resume/{resume_id}"] = _entry(
            await client.get(f"{API}/resumes/{resume_id}/candidate")
        )
    progress(
        f"capture_api: {len(responses)} responses "
        f"({len(candidate_ids)} candidate ids, {len(resume_ids)} resumes)"
    )
    return {
        "meta": {
            "version": CAPTURE_VERSION,
            "base_url": str(client.base_url),
            "captured_at": datetime.now(UTC).isoformat(),
            "campaigns": selected,
        },
        "responses": responses,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.devtools.capture_api",
        description="Capture every legacy GET endpoint's responses (read-switch equivalence).",
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--campaigns", default="all", help="all (default), the first N, or comma-separated ids"
    )
    parser.add_argument(
        "--ids-from",
        type=Path,
        default=None,
        help="a previous capture: also request every candidate id it saw",
    )
    args = parser.parse_args(argv)

    extra: list[str] = []
    if args.ids_from is not None:
        extra = candidate_ids_of(json.loads(args.ids_from.read_text(encoding="utf-8")))

    async def run() -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=args.base_url, timeout=60) as client:
            return await capture(client, campaigns=args.campaigns, extra_candidate_ids=extra)

    result = asyncio.run(run())
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
    print(f"capture_api: wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
