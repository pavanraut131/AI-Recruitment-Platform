import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Import all models so Alembic can detect them (every table must be on Base.metadata)
import app.modules.hiring.models  # noqa: F401
import app.modules.identity.models  # noqa: F401
import app.modules.ingestion.models  # noqa: F401
import app.modules.talent.models  # noqa: F401
from app.platform.config import settings
from app.platform.database import Base

config = context.config
# Migrations run as the owner role, hireflow_owner (EPIC-02 spec §6.2, §7.2): it owns the schema
# and every table, and row-level security lets it see every agency (the `maintenance` policy).
# Never the application role: the tables would not be its own to alter.
config.set_main_option("sqlalchemy.url", settings.migration_database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
