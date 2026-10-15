"""The ``AI_PROVIDER`` setting and the ``get_ai_service()`` factory."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.ai import service
from app.modules.ai.fake import FakeAIService
from app.modules.ai.gemini import GeminiAIService
from app.platform import config


def test_default_provider_is_gemini() -> None:
    assert config.Settings.model_fields["AI_PROVIDER"].default == "gemini"
    assert isinstance(service.get_ai_service(), GeminiAIService)


def test_ai_provider_fake_env_var_wires_the_fake_through_the_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_PROVIDER", "fake")
    settings = config.Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.AI_PROVIDER == "fake"

    monkeypatch.setattr(service, "settings", settings)
    assert isinstance(service.get_ai_service(), FakeAIService)


def test_fake_ai_fixture_selects_the_fake(fake_ai: FakeAIService) -> None:
    assert isinstance(service.get_ai_service(), FakeAIService)


def test_unknown_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "openai")

    with pytest.raises(ValidationError, match="AI_PROVIDER"):
        config.Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("impl", [GeminiAIService, FakeAIService])
def test_implementations_satisfy_the_protocol(impl: type[service.AIService]) -> None:
    ai: service.AIService = impl()
    assert callable(ai.extract_profile)
    assert callable(ai.score)
    assert callable(ai.parse_job_description)
    assert callable(ai.transcribe_page_images)
