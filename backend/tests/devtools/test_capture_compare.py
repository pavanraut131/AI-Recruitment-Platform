"""The read-switch equivalence tooling: ``app.devtools.capture_api`` and ``compare_api``.

The capture runs against the app (ASGI, the test's rolled-back session). The comparison is
checked on hand-made captures that contain what the switch is expected to change: a merged
legacy id, an unprocessed placeholder row and the global count, next to real differences.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.devtools import capture_api, compare_api
from tests.factories import at, make_campaign, make_candidate, make_resume


# ── capture_api ───────────────────────────────────────────────
async def test_a_capture_records_every_legacy_get_endpoint_and_equals_itself(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    campaign = await make_campaign(db_session)
    made = []
    for i in range(3):
        resume = await make_resume(
            db_session, campaign, original_filename=f"cv{i}.pdf", uploaded_at=at(i)
        )
        made.append(await make_candidate(db_session, resume=resume, score=80.0 - i))
    pending = await make_resume(db_session, campaign, original_filename="new.pdf", status="pending")
    campaign_id, ids = str(campaign.id), [str(a.id) for a in made]

    first = await capture_api.capture(client, progress=lambda _: None)
    second = await capture_api.capture(client, progress=lambda _: None)

    responses = first["responses"]
    assert {
        "campaigns",
        "stats",
        f"campaign/{campaign_id}",
        f"campaign_stats/{campaign_id}",
    } <= set(responses)
    for name in capture_api.LIST_FILTERS:
        assert f"list/{campaign_id}/{name}" in responses
    for name in capture_api.EXPORT_FILTERS:
        assert responses[f"export/{campaign_id}/{name}"]["headers"]["content-type"].startswith(
            "text/csv"
        )
    assert [r["id"] for r in responses[f"list/{campaign_id}/all"]["body"]] == ids
    assert {f"detail/{i}" for i in ids} <= set(responses)
    assert responses[f"by_resume/{pending.id}"]["status"] == 404
    assert capture_api.candidate_ids_of(first) == sorted(ids)

    result = compare_api.compare(first, second)
    assert result.ok, result.render()
    assert result.identical == len(responses)


async def test_ids_from_an_earlier_capture_are_requested_too(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    unknown = "00000000-0000-0000-0000-000000000001"
    result = await capture_api.capture(
        client, extra_candidate_ids=[unknown], progress=lambda _: None
    )
    assert result["responses"][f"detail/{unknown}"]["status"] == 404


def test_ties_are_put_in_id_order_and_nothing_else_moves() -> None:
    rows = [
        {"id": "b", "score": 90.0},
        {"id": "a", "score": 90.0},
        {"id": "c", "score": 80.0},
        {"id": "e", "score": None},
        {"id": "d", "score": None},
    ]
    assert [r["id"] for r in capture_api.normalise_rows(rows)] == ["a", "b", "c", "d", "e"]
    csv_text = "Name,Email,Score\r\nB,b@x,90\r\nA,a@x,90\r\nC,c@x,80\r\n"
    assert capture_api.normalise_csv(csv_text) == [
        "Name,Email,Score",
        "A,a@x,90",
        "B,b@x,90",
        "C,c@x,80",
        "",
    ]


# ── compare_api ───────────────────────────────────────────────
C = "c0000000-0000-0000-0000-000000000000"
X, Y, Z, P = (f"{n}0000000-0000-0000-0000-000000000000" for n in "abde")
R1, R2, R3, R4 = (f"f{n}000000-0000-0000-0000-000000000000" for n in "1234")


def _detail(cid: str, rid: str, name: str, score: float | None) -> dict[str, Any]:
    email = f"{name}@x" if name else None
    return {"id": cid, "resume_id": rid, "name": name or None, "email": email, "score": score}


def _before() -> dict[str, Any]:
    """Legacy: X and Y are one person's two resumes; P is a resume still processing."""
    rows = [
        _detail(X, R1, "x", 90.0),
        _detail(Y, R2, "x", 80.0),
        _detail(Z, R3, "z", 70.0),
        _detail(P, R4, "", None),
    ]
    responses: dict[str, Any] = {
        "campaigns": {"status": 200, "body": [{"id": C}]},
        "stats": {"status": 200, "body": {"total_resumes": 4, "total_candidates": 4}},
        f"resumes/{C}": {
            "status": 200,
            "body": [
                {"id": R1, "status": "done"},
                {"id": R2, "status": "done"},
                {"id": R3, "status": "done"},
                {"id": R4, "status": "processing"},
            ],
        },
        f"list/{C}/all": {"status": 200, "body": rows},
        f"campaign_stats/{C}": {
            "status": 200,
            "body": {
                "total_resumes": 4,
                "total_processed": 3,
                "strong_match": 2,
                "top_skills": [{"skill": "Go", "count": 2}, {"skill": "Py", "count": 2}],
            },
        },
        f"export/{C}/all": {
            "status": 200,
            "headers": {"content-type": "text/csv"},
            "body": ["Name,Email,Score", "x,x@x,90", "x,x@x,80", "z,z@x,70", ",,", ""],
        },
    }
    for row in rows:
        responses[f"detail/{row['id']}"] = {"status": 200, "body": row}
        responses[f"by_resume/{row['resume_id']}"] = {"status": 200, "body": row}
    return {"responses": responses}


def _after() -> dict[str, Any]:
    """Switched: Y is merged into X's application (which took Y's newer resume); P has no row."""
    after = copy.deepcopy(_before())
    r = after["responses"]
    merged = _detail(X, R2, "x", 80.0)
    r[f"list/{C}/all"]["body"] = [merged, _detail(Z, R3, "z", 70.0)]
    for key in (f"detail/{X}", f"detail/{Y}", f"by_resume/{R1}", f"by_resume/{R2}"):
        r[key] = {"status": 200, "body": merged}
    for key in (f"detail/{P}", f"by_resume/{R4}"):
        r[key] = {"status": 404, "body": {"detail": "Candidate not yet processed"}}
    r["stats"]["body"]["total_candidates"] = 2
    r[f"campaign_stats/{C}"]["body"]["strong_match"] = 1
    r[f"export/{C}/all"]["body"] = ["Name,Email,Score", "x,x@x,80", "z,z@x,70", ""]
    return after


def test_the_switch_s_expected_differences_are_reported_not_failed() -> None:
    result = compare_api.compare(_before(), _after())

    assert result.ok, result.render()
    assert result.merged_ids == 1
    assert sorted(result.expected[compare_api.MERGED]) == sorted(
        [
            f"detail/{X}",
            f"detail/{Y}",
            f"by_resume/{R1}",
            f"by_resume/{R2}",
            f"list/{C}/all",
            f"campaign_stats/{C}",
            f"export/{C}/all",
            "stats",
        ]
    )
    assert sorted(result.expected[compare_api.UNPROCESSED]) == [f"by_resume/{R4}", f"detail/{P}"]
    assert f"detail/{Z}" not in result.differences
    assert "expected (merged legacy id):       8 (1 merged legacy ids)" in result.render()


@pytest.mark.parametrize(
    ("change", "where"),
    [
        (lambda r: r[f"detail/{Z}"]["body"].update(score=71.0), f"detail/{Z}: .body.score"),
        (lambda r: r[f"list/{C}/all"]["body"][1].update(name="zz"), f"list/{C}/all"),
        (lambda r: r[f"campaign_stats/{C}"]["body"].update(total_resumes=5), "campaign_stats"),
        (lambda r: r["stats"]["body"].update(total_candidates=3), "stats: total_candidates"),
        (lambda r: r[f"export/{C}/all"]["body"].insert(2, "q,q@x,1"), f"export/{C}/all"),
        (lambda r: r.pop(f"detail/{Y}"), f"detail/{Y}: only in before"),
    ],
)
def test_any_other_difference_fails(change: Any, where: str) -> None:
    after = _after()
    change(after["responses"])

    result = compare_api.compare(_before(), after)

    assert not result.ok
    assert any(d.startswith(where) for d in result.differences), result.differences


def test_top_skills_with_equal_counts_may_come_in_any_order() -> None:
    after = copy.deepcopy(_before())
    after["responses"][f"campaign_stats/{C}"]["body"]["top_skills"].reverse()

    result = compare_api.compare(_before(), after)

    assert result.ok and not result.expected[compare_api.MERGED], result.render()


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before, after, broken = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "c.json"
    before.write_text(json.dumps(_before()), encoding="utf-8")
    after.write_text(json.dumps(_after()), encoding="utf-8")
    broken.write_text("{}", encoding="utf-8")

    assert compare_api.main([str(before), str(after)]) == 0
    assert capsys.readouterr().out.rstrip().endswith("PASSED")
    changed = _after()
    changed["responses"][f"detail/{Z}"]["body"]["score"] = 1.0
    after.write_text(json.dumps(changed), encoding="utf-8")
    assert compare_api.main([str(before), str(after)]) == 1
    assert capsys.readouterr().out.rstrip().endswith("FAILED")
    assert compare_api.main([str(before), str(broken)]) == 2


def test_two_switched_captures_compare_exactly_merged_ids_included() -> None:
    """Across the E0-20 contract both captures serve the switched API: a merged id redirects on
    both sides, so it is not an expected difference and its responses are compared exactly."""
    result = compare_api.compare(_after(), _after())
    assert result.ok and result.merged_ids == 0, result.render()
    assert not any(result.expected.values())

    changed = _after()
    changed["responses"][f"detail/{Y}"]["body"]["score"] = 1.0
    result = compare_api.compare(_after(), changed)
    assert not result.ok
    assert any(d.startswith(f"detail/{Y}") for d in result.differences), result.differences
