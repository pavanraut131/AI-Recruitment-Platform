from functools import lru_cache
from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    APP_ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "change_me"
    API_V1_PREFIX: str = "/api/v1"
    API_V2_PREFIX: str = "/api/v2"

    # Database (EPIC-02 spec §6.2, §7.2). The API (async) and the Celery workers (sync) connect
    # as the application role, hireflow_app: under row-level security, owning nothing; they
    # refuse to start as anything more (app.platform.rls.check_app_role).
    DATABASE_URL: str
    SYNC_DATABASE_URL: str
    # The owner role, hireflow_owner: Alembic and the operator commands that span agencies
    # (app.platform.maintenance). Optional for the API and the workers, which never use it.
    MIGRATION_DATABASE_URL: str | None = None

    # Redis / Celery
    REDIS_URL: str = "redis://redis:6379/0"
    CELERY_BROKER_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/1"

    # MinIO / S3
    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin_secret"
    MINIO_BUCKET_NAME: str = "resumes"
    MINIO_USE_SSL: bool = False

    # AI provider behind app.modules.ai.service.get_ai_service(): "gemini" (Google Gemini, needs
    # GEMINI_API_KEY) or "fake" (deterministic offline answers for local dev and smoke tests).
    AI_PROVIDER: Literal["gemini", "fake"] = "gemini"

    # Gemini. Required (non-blank) when AI_PROVIDER is "gemini", optional otherwise; see
    # _require_gemini_key.
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # Upload limits
    MAX_FILE_SIZE_MB: int = 10
    MAX_FILES_PER_BATCH: int = 50

    # Identity matching: region assumed for phone numbers written without a country code
    # (ISO 3166-1 alpha-2, e.g. "IN", "US"). See app/modules/talent/identity.py.
    PHONE_DEFAULT_REGION: str = "IN"

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000"

    @model_validator(mode="after")
    def _require_gemini_key(self) -> Self:
        """Fail at start-up, not on the first resume, when Gemini is selected without a key."""
        if self.AI_PROVIDER == "gemini" and not (self.GEMINI_API_KEY or "").strip():
            raise ValueError(
                "GEMINI_API_KEY is required when AI_PROVIDER=gemini (the default). Set "
                "GEMINI_API_KEY to your Google Gemini API key, or set AI_PROVIDER=fake to run "
                "without Gemini."
            )
        return self

    @property
    def migration_database_url(self) -> str:
        """``MIGRATION_DATABASE_URL``; raises with what to set when it is missing."""
        url = (self.MIGRATION_DATABASE_URL or "").strip()
        if not url:
            raise RuntimeError(
                "MIGRATION_DATABASE_URL is not set. Migrations and the operator commands connect "
                "as the owner role (hireflow_owner), e.g. "
                "postgresql://hireflow_owner:<password>@postgres:5432/<db>; docker-compose.yml "
                "sets it for the backend services (docs/runbooks/tenancy-rls.md)."
            )
        return url

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
