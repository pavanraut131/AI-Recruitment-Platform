"""In-memory stand-ins for external services, installed by autouse fixtures in ``conftest.py``.

- :class:`CeleryRecorder` records task dispatch instead of sending it to a broker.
- :class:`InMemoryStorage` replaces the MinIO functions in ``app.core.storage``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import ClientError


# ── Celery ────────────────────────────────────────────────────
@dataclass
class CeleryCall:
    """One recorded ``apply_async`` / ``delay`` call.

    Has an ``id`` so code that reads ``result.id`` from the returned value keeps working.
    """

    task: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    options: dict[str, Any]
    id: str


@dataclass
class CeleryRecorder:
    calls: list[CeleryCall] = field(default_factory=list)

    def record(
        self,
        task: str,
        args: Any = None,
        kwargs: Any = None,
        task_id: str | None = None,
        **options: Any,
    ) -> CeleryCall:
        call = CeleryCall(
            task=task,
            args=tuple(args or ()),
            kwargs=dict(kwargs or {}),
            options=options,
            id=task_id or str(uuid.uuid4()),
        )
        self.calls.append(call)
        return call

    def for_task(self, name: str) -> list[CeleryCall]:
        """Calls for one task, by full name, e.g. ``"workers.extract_task.extract_resume"``."""
        return [c for c in self.calls if c.task == name]

    def __len__(self) -> int:
        return len(self.calls)


# ── Object storage (MinIO) ────────────────────────────────────
@dataclass
class StoredObject:
    body: bytes
    content_type: str


class InMemoryStorage:
    """Same function signatures and observable behaviour as ``app.core.storage``."""

    def __init__(self, bucket: str) -> None:
        self.bucket = bucket
        self.objects: dict[str, StoredObject] = {}
        self.bucket_ensured = False

    def ensure_bucket_exists(self) -> None:
        self.bucket_ensured = True

    def upload_file(
        self, file_bytes: bytes, object_key: str, content_type: str = "application/pdf"
    ) -> str:
        self.objects[object_key] = StoredObject(bytes(file_bytes), content_type)
        return object_key

    def download_file(self, object_key: str) -> bytes:
        try:
            return self.objects[object_key].body
        except KeyError:
            # Same error type and code boto3 raises for a missing key.
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}},
                "GetObject",
            ) from None

    def delete_file(self, object_key: str) -> None:
        # S3/MinIO DeleteObject succeeds for missing keys too.
        self.objects.pop(object_key, None)

    def get_presigned_url(self, object_key: str, expires_in: int = 3600) -> str:
        return f"http://storage.test/{self.bucket}/{object_key}?X-Amz-Expires={expires_in}"
