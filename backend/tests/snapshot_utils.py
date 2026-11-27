"""The snapshot serializer of the contract tests (``tests/contract_v2``), and helpers that turn
responses into plain data.

``MaskedAmberExtension`` is syrupy's default Amber format with one change: before the data is
serialized it goes through a :class:`Masker`, which replaces the values that change between runs
with stable placeholders:

* UUIDs (``uuid.UUID`` objects and UUIDs anywhere inside strings, e.g. object keys, file names,
  Celery task ids) become ``<uuid:1>``, ``<uuid:2>``, ... numbered by **first appearance within
  the test**. The same UUID always gets the same number in one test, across all of that test's
  snapshot assertions, so relationships stay visible (a resume id in the upload response is the
  same ``<uuid:3>`` as in the stored object key and the Celery call).
* datetimes (``datetime`` objects and ISO-8601 timestamps inside strings) become ``<datetime>``.
* the fixed id of the default agency (``tests.default_agency.DEFAULT_AGENCY_ID``, the same in
  every run: the E2-01 migration inserts it) becomes ``<default-agency>``, not a number: it
  appears in the Celery calls' ``agency_id`` and in new storage keys, and naming it keeps the
  other numbers stable. Likewise the default agency's client (``DEFAULT_CLIENT_ID``) becomes
  ``<default-client>``: every ``Campaign`` shows its client.

Dict keys are visited in sorted order (the order Amber prints them in), so numbering follows the
order you read the snapshot in.

The ``snapshot`` fixture in ``tests/contract_v2/conftest.py`` uses this extension, so tests
simply ``assert data == snapshot``. Until E2-12 the ``/api/v1`` characterization tests used it
too (they were removed with v1).
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Any

import httpx
from syrupy.extensions.amber import AmberSnapshotExtension

from tests import default_agency

DEFAULT_AGENCY_ID = str(default_agency.DEFAULT_AGENCY_ID)
DEFAULT_AGENCY_PLACEHOLDER = "<default-agency>"
DEFAULT_CLIENT_ID = str(default_agency.DEFAULT_CLIENT_ID)
DEFAULT_CLIENT_PLACEHOLDER = "<default-client>"
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
DATETIME_PLACEHOLDER = "<datetime>"


class Masker:
    """Replaces run-dependent values with stable placeholders (see module docstring)."""

    def __init__(self) -> None:
        self._uuids: dict[str, str] = {}

    def uuid_placeholder(self, value: str | uuid.UUID) -> str:
        key = str(value).lower()
        if key == DEFAULT_AGENCY_ID:
            return DEFAULT_AGENCY_PLACEHOLDER
        if key == DEFAULT_CLIENT_ID:
            return DEFAULT_CLIENT_PLACEHOLDER
        if key not in self._uuids:
            self._uuids[key] = f"<uuid:{len(self._uuids) + 1}>"
        return self._uuids[key]

    def mask_str(self, value: str) -> str:
        value = UUID_RE.sub(lambda m: self.uuid_placeholder(m.group(0)), value)
        return DATETIME_RE.sub(DATETIME_PLACEHOLDER, value)

    def mask(self, data: Any) -> Any:
        if isinstance(data, dict):
            # Sort as Amber prints, but ignore UUIDs inside keys (they differ per run); ties keep
            # insertion order (sorted() is stable), so numbering is deterministic.
            ordered = sorted(data, key=lambda k: UUID_RE.sub("", str(k)))
            return {self.mask(key): self.mask(data[key]) for key in ordered}
        if isinstance(data, list):
            return [self.mask(item) for item in data]
        if isinstance(data, tuple):
            return tuple(self.mask(item) for item in data)
        if isinstance(data, uuid.UUID):
            return self.uuid_placeholder(data)
        if isinstance(data, datetime | date):
            return DATETIME_PLACEHOLDER
        if isinstance(data, str):
            return self.mask_str(data)
        return data


class MaskedAmberExtension(AmberSnapshotExtension):
    """Amber snapshots with :class:`Masker` applied. One masker per extension instance, and the
    ``snapshot`` fixture creates one extension instance per test."""

    _masker: Masker | None = None

    def serialize(self, data: Any, **kwargs: Any) -> str:
        if self._masker is None:
            self._masker = Masker()
        return super().serialize(self._masker.mask(data), **kwargs)


# ── Helpers that turn observable behaviour into plain data ─────
def response_data(resp: httpx.Response, *headers: str) -> dict[str, Any]:
    """Status code, body (JSON when possible, else text) and the named response headers.

    ``content-type`` is always included.
    """
    try:
        body: Any = resp.json()
    except ValueError:
        body = resp.text
    names = ("content-type", *headers)
    return {
        "status_code": resp.status_code,
        "headers": {name: resp.headers.get(name) for name in names},
        "body": body,
    }


def celery_calls_data(recorder: Any) -> list[dict[str, Any]]:
    """Recorded Celery dispatches (``tests.fakes.CeleryRecorder``) as plain data."""
    return [
        {"task": c.task, "args": list(c.args), "kwargs": c.kwargs, "options": c.options}
        for c in recorder.calls
    ]
