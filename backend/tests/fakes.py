"""In-memory stand-ins for external services, installed by autouse fixtures in ``conftest.py``.

- :class:`CeleryRecorder` records task dispatch instead of sending it to a broker.
- :class:`InMemoryStorage` replaces the MinIO functions in ``app.platform.storage``.
- :class:`FakeGemini` replaces ``google.generativeai.GenerativeModel``.
- :class:`FakeRedis` gives every ``redis.Redis.from_url`` / ``redis.asyncio.Redis.from_url`` call a
  fakeredis client on one shared in-memory server, so pub/sub works across sync and async clients.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import fakeredis
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
        """Calls for one task, by its registered name, e.g. ``tasks.EXTRACT_TASK_NAME``."""
        return [c for c in self.calls if c.task == name]

    def __len__(self) -> int:
        return len(self.calls)


# ── Object storage (MinIO) ────────────────────────────────────
@dataclass
class StoredObject:
    body: bytes
    content_type: str


class InMemoryStorage:
    """Same function signatures and observable behaviour as ``app.platform.storage``."""

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


# ── Gemini (google.generativeai) ──────────────────────────────
class GeminiNotConfiguredError(RuntimeError):
    """Raised when code under test calls Gemini but the test did not script a response."""


@dataclass
class GeminiCall:
    model: str
    prompt: Any


@dataclass
class FakeGeminiResponse:
    text: str


class FakeGemini:
    """Scriptable stand-in for ``google.generativeai.GenerativeModel``.

    ``respond_with(text)`` makes later calls return ``text`` as ``response.text``;
    ``respond_with(exc)`` makes them raise ``exc``. Until a response is scripted every call raises
    :class:`GeminiNotConfiguredError`, so an unexpected AI call fails loudly instead of reaching
    the network. Every call is recorded in ``calls``.
    """

    def __init__(self) -> None:
        self.calls: list[GeminiCall] = []
        self._response: str | BaseException = GeminiNotConfiguredError(
            "Gemini was called but the test did not script a response (fake_gemini.respond_with)"
        )

    def respond_with(self, response: str | BaseException) -> None:
        self._response = response

    def answer(self, model: str, prompt: Any) -> FakeGeminiResponse:
        self.calls.append(GeminiCall(model=model, prompt=prompt))
        if isinstance(self._response, BaseException):
            raise self._response
        return FakeGeminiResponse(text=self._response)

    def GenerativeModel(self, model_name: str, **_: Any) -> FakeGenerativeModel:  # noqa: N802
        return FakeGenerativeModel(self, model_name)


class FakeGenerativeModel:
    def __init__(self, fake: FakeGemini, model_name: str) -> None:
        self._fake = fake
        self.model_name = model_name

    def generate_content(self, prompt: Any, **_: Any) -> FakeGeminiResponse:
        return self._fake.answer(self.model_name, prompt)

    async def generate_content_async(self, prompt: Any, **_: Any) -> FakeGeminiResponse:
        return self._fake.answer(self.model_name, prompt)


# ── Redis ─────────────────────────────────────────────────────
class FakeRedis:
    """One in-memory Redis server per test; hands out sync and async fakeredis clients for it.

    Set ``server.connected = False`` to simulate Redis being down (commands raise
    ``redis.exceptions.ConnectionError``).
    """

    def __init__(self) -> None:
        self.server = fakeredis.FakeServer()

    def sync_client(self, url: str = "", **kwargs: Any) -> fakeredis.FakeRedis:
        return fakeredis.FakeRedis(server=self.server, **kwargs)

    def async_client(self, url: str = "", **kwargs: Any) -> fakeredis.FakeAsyncRedis:
        return fakeredis.FakeAsyncRedis(server=self.server, **kwargs)
