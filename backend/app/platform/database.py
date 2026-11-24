from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

# Imported for its side effect too: the session listener that puts a bound session's agency in
# every transaction (app.platform.tenancy, E2-03) is registered whenever a session can exist.
from app.platform import tenancy
from app.platform.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# Development and tests: assert that no pooled connection carries an agency into its next
# checkout (spec §6.3).
tenancy.guard_in_development(engine.sync_engine, settings.APP_ENV)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
