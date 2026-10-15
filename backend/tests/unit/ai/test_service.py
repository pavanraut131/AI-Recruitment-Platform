"""The ``AI_PROVIDER`` / ``GEMINI_API_KEY`` settings and the ``get_ai_service()`` factory."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

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


# ── GEMINI_API_KEY is required only for AI_PROVIDER=gemini (E0-13) ───────────


def test_fake_provider_needs_no_gemini_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "fake")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    settings = config.Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.AI_PROVIDER == "fake"
    assert settings.GEMINI_API_KEY is None


@pytest.mark.parametrize("key", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_gemini_provider_without_a_key_fails_at_settings_load(
    monkeypatch: pytest.MonkeyPatch, key: str | None
) -> None:
    monkeypatch.delenv("AI_PROVIDER", raising=False)  # the default, gemini
    if key is None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("GEMINI_API_KEY", key)

    with pytest.raises(ValidationError) as excinfo:
        config.Settings(_env_file=None)  # type: ignore[call-arg]

    message = str(excinfo.value)
    assert "GEMINI_API_KEY is required when AI_PROVIDER=gemini" in message
    assert "AI_PROVIDER=fake" in message  # tells you the way out


def test_gemini_provider_with_a_key_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    assert config.Settings(_env_file=None).GEMINI_API_KEY == "k"  # type: ignore[call-arg]


BACKEND_DIR = Path(__file__).resolve().parents[3]

# Runs in a fresh interpreter, because `app` reads its settings once, at import.
_IMPORT_APP_WITHOUT_A_KEY = textwrap.dedent(
    """
    import google.generativeai as genai

    def no_configure(**kwargs):
        raise AssertionError("google.generativeai.configure() called")

    genai.configure = no_configure  # importing the app must not configure the SDK

    import app.main  # noqa: F401  (imports every module, the Gemini adapter included)
    import app.modules.ai.gemini  # noqa: F401
    from app.modules.ai.fake import FakeAIService
    from app.modules.ai.service import get_ai_service
    from app.platform.config import settings

    assert settings.AI_PROVIDER == "fake" and settings.GEMINI_API_KEY is None
    ai = get_ai_service()
    assert isinstance(ai, FakeAIService)
    print(ai.extract_profile("Asha Sharma, Python developer, 4 years").skills)
    """
)


def test_app_imports_and_runs_with_fake_provider_and_no_key_at_all(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k not in {"GEMINI_API_KEY", "AI_PROVIDER"}}
    env |= {"AI_PROVIDER": "fake", "PYTHONPATH": str(BACKEND_DIR)}

    # cwd=tmp_path: no backend/.env can supply a key.
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_APP_WITHOUT_A_KEY],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['Python']"
