"""Deprecated re-export shim; use :mod:`app.platform.config`."""

# TODO(E0-10): remove
from app.platform.config import Settings, get_settings, settings


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    APP_ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "change_me"
    API_V1_PREFIX: str = "/api/v1"

    # Database
    DATABASE_URL: str
    SYNC_DATABASE_URL: str

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

    # Gemini
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # Upload limits
    MAX_FILE_SIZE_MB: int = 10
    MAX_FILES_PER_BATCH: int = 50

    # Identity matching: region assumed for phone numbers written without a country code
    # (ISO 3166-1 alpha-2, e.g. "IN", "US"). See app/modules/talent/identity.py.
    PHONE_DEFAULT_REGION: str = "IN"

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
