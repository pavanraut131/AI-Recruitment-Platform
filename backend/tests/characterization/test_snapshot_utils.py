"""The masking serializer itself: stable placeholders, numbered by first appearance."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from tests.characterization.snapshot_utils import Masker

A = "3f2b8c1e-9d4a-4e6b-8a7c-1b2c3d4e5f60"
B = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def test_uuids_are_numbered_by_first_appearance_and_reused() -> None:
    masker = Masker()
    data = {
        "id": A,
        "parent": uuid.UUID(B),
        "path": f"campaigns/{B}/resumes/{A}/cv.pdf",
        "refs": [A.upper(), B],
    }
    assert masker.mask(data) == {
        "id": "<uuid:1>",
        "parent": "<uuid:2>",
        "path": "campaigns/<uuid:2>/resumes/<uuid:1>/cv.pdf",
        "refs": ["<uuid:1>", "<uuid:2>"],
    }
    # Numbering carries over to later calls on the same masker (one masker per test).
    assert masker.mask([B, A]) == ["<uuid:2>", "<uuid:1>"]


def test_dict_keys_are_visited_in_sorted_order() -> None:
    assert Masker().mask({"b": A, "a": B}) == {"a": "<uuid:1>", "b": "<uuid:2>"}


def test_datetimes_are_masked() -> None:
    masker = Masker()
    assert masker.mask(
        {
            "obj": datetime(2025, 1, 1, tzinfo=UTC),
            "iso": "2025-01-06T09:00:00.123456Z",
            "offset": "2025-01-06T09:00:00+00:00",
            "naive": "2025-01-06 09:00:00",
            "embedded": "ping - 2025-01-06 09:00:00.5+00:00!",
        }
    ) == {
        "embedded": "ping - <datetime>!",
        "iso": "<datetime>",
        "naive": "<datetime>",
        "obj": "<datetime>",
        "offset": "<datetime>",
    }


def test_other_values_are_untouched() -> None:
    data = {"n": 1, "f": 52.5, "none": None, "flag": True, "s": "2025", "t": ("x", 1)}
    assert Masker().mask(data) == data
