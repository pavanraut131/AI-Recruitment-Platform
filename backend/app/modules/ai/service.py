"""ai service: the public interface of the ``ai`` module (ports and adapters).

Every language-model call in the app goes through an :class:`AIService`. Callers get one from
:func:`get_ai_service`, which picks the implementation from the ``AI_PROVIDER`` setting:

- ``gemini`` (default): :class:`~app.modules.ai.gemini.GeminiAIService`, Google Gemini.
- ``fake``: :class:`~app.modules.ai.fake.FakeAIService`, deterministic answers derived from the
  input text, no network and no API key. For local development, end-to-end smoke tests and unit
  tests.

Provider errors never leak out of this module: callers catch :class:`AIQuotaExceeded` (the
provider's quota is used up; retry after its ``retry_after``) and :class:`AIConfigurationError`
(the provider cannot be used as configured), so nothing outside ``app.modules.ai`` imports a
provider SDK such as ``google.*`` (import-linter enforces it). Any other exception means the call
failed (network, invalid model output, ...) and may be retried.

The methods the Celery workers call are synchronous (the workers have no event loop);
:meth:`AIService.parse_job_description` serves an HTTP request and is a coroutine.
"""

from collections.abc import Sequence
from typing import Protocol

from app.modules.ai.schemas import ExtractionResult, JDParseResult, JobRequirements, ScoringResult
from app.platform.config import settings

__all__ = [
    "AIConfigurationError",
    "AIError",
    "AIQuotaExceeded",
    "AIService",
    "get_ai_service",
]


class AIError(Exception):
    """Base class of the errors this module raises on purpose."""


class AIConfigurationError(AIError):
    """The provider cannot be used as configured (e.g. no API key). The message is user-facing."""


class AIQuotaExceeded(AIError):
    """The provider's quota is exhausted (Gemini ``ResourceExhausted``). Raised on the first
    quota error, without waiting: the caller decides when to try again (spec §5.5).

    ``retry_after`` is how many seconds the provider asked us to wait before retrying, when it
    said so (Gemini's ``RetryInfo.retry_delay``), else ``None``.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AIService(Protocol):
    """What the rest of the app may ask a language model to do."""

    def extract_profile(self, text: str) -> ExtractionResult:
        """Structured candidate profile from raw resume text."""
        ...

    def score(self, profile: ExtractionResult, requirements: JobRequirements) -> ScoringResult:
        """Score and categorise ``profile`` against a role's requirements."""
        ...

    async def parse_job_description(self, text: str) -> JDParseResult:
        """Role title, seniority and required skills from a job description."""
        ...

    def transcribe_page_images(self, images: Sequence[bytes]) -> str:
        """OCR: the text of scanned pages (PNG images, one per page), pages joined by newlines.

        Used for PDFs without a text layer.
        """
        ...


def get_ai_service() -> AIService:
    """The :class:`AIService` selected by ``settings.AI_PROVIDER``.

    Read on every call (implementations are cheap to create and hold no connections), so a
    changed setting, e.g. in a test, takes effect immediately.
    """
    # Imported here: the implementations import this module for the error types.
    if settings.AI_PROVIDER == "fake":
        from app.modules.ai.fake import FakeAIService

        return FakeAIService()

    from app.modules.ai.gemini import GeminiAIService

    return GeminiAIService()
