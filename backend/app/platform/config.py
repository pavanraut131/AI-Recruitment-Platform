from datetime import timedelta
from functools import lru_cache
from typing import Final, Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Environments where development-only settings are allowed (AUTH_REQUIRED=false, insecure
# cookies, a template SECRET_KEY). Anything else (staging, production, ...) is treated as a
# deployment and refuses them at start-up (EPIC-02 spec §10).
DEV_ENVIRONMENTS: Final = frozenset({"development", "test"})

# SECRET_KEY values that are never acceptable in a deployment: the defaults and the value of
# .env.example. Each also fails the length check below except the template, which is long.
TEMPLATE_SECRET_KEYS: Final = frozenset(
    {"change_me", "change_me_in_production_use_openssl_rand_hex_32", "changeme", "secret"}
)
MIN_SECRET_KEY_BYTES: Final = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    APP_ENV: str = "development"
    DEBUG: bool = True
    # Signs the CSRF tokens (and keys the hashes of rate-limit identifiers). Outside development
    # and test it must be at least 32 bytes and not a template value (_check_deployment_safety);
    # generate one with `openssl rand -hex 32`.
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

    # CORS. Also the allowed origins of the CSRF Origin check on /api/v2 (app.platform.csrf).
    CORS_ORIGINS: str = "http://localhost:3000"

    # ── Authentication (EPIC-02 §4) ──
    # Every /api/v2 route except the anonymous auth endpoints requires a session. False makes a
    # request **without** a session cookie act as the default agency's owner, so the frontend
    # (no login screen until E2-09) and the E2E stack keep working. Development and test only:
    # refused anywhere else. Temporary: removed by E2-09.
    AUTH_REQUIRED: bool = True
    # The Secure flag on the session and CSRF cookies; with it the cookies get the __Host-
    # prefix (__Host-hf_session, __Host-hf_csrf), without it they are hf_session / hf_csrf so
    # they work over plain http://localhost. Unset: Secure everywhere but development and test.
    # False is refused outside development and test.
    SESSION_COOKIE_SECURE: bool | None = None
    # Sessions end after this long without a request, and this long after sign-in in any case.
    # Seconds or ISO 8601 durations (e.g. 28800 or PT8H).
    SESSION_IDLE_TIMEOUT: timedelta = timedelta(hours=8)
    SESSION_ABSOLUTE_TIMEOUT: timedelta = timedelta(days=7)
    # Argon2id cost (RFC 9106 low-memory profile: 64 MiB, 3 passes, 4 lanes). Raising them takes
    # effect for existing users at their next sign-in (rehash on login).
    PASSWORD_HASH_MEMORY_KIB: int = 65536
    PASSWORD_HASH_TIME_COST: int = 3
    PASSWORD_HASH_PARALLELISM: int = 4
    # Login throttling (never lockout): failed sign-ins per email, and attempts per client IP,
    # per window. Over a limit is 429 with Retry-After.
    LOGIN_MAX_FAILURES_PER_EMAIL: int = 5
    LOGIN_MAX_ATTEMPTS_PER_IP: int = 30
    LOGIN_RATE_LIMIT_WINDOW: timedelta = timedelta(minutes=15)

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

    @model_validator(mode="after")
    def _check_deployment_safety(self) -> Self:
        """Outside development and test, refuse the settings that are only safe on a laptop
        (EPIC-02 spec §10): a weak or template ``SECRET_KEY``, ``AUTH_REQUIRED=false`` and
        ``SESSION_COOKIE_SECURE=false``."""
        if self.is_development:
            return self
        problems = []
        key = self.SECRET_KEY.strip()
        if key.lower() in TEMPLATE_SECRET_KEYS or len(key.encode()) < MIN_SECRET_KEY_BYTES:
            problems.append(
                f"SECRET_KEY must be at least {MIN_SECRET_KEY_BYTES} bytes and not a template "
                "value (generate one with `openssl rand -hex 32`)"
            )
        if not self.AUTH_REQUIRED:
            problems.append("AUTH_REQUIRED=false is allowed only in development and test")
        if self.SESSION_COOKIE_SECURE is False:
            problems.append("SESSION_COOKIE_SECURE=false is allowed only in development and test")
        if problems:
            raise ValueError(f"APP_ENV={self.APP_ENV}: " + "; ".join(problems))
        return self

    @property
    def is_development(self) -> bool:
        """``APP_ENV`` is ``development`` or ``test``."""
        return self.APP_ENV.strip().lower() in DEV_ENVIRONMENTS

    @property
    def session_cookie_secure(self) -> bool:
        """Whether the session and CSRF cookies are ``Secure`` (and ``__Host-`` prefixed)."""
        if self.SESSION_COOKIE_SECURE is None:
            return not self.is_development
        return self.SESSION_COOKIE_SECURE

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
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
