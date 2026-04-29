"""Alembic environment configuration for lolday backend.

Reads DATABASE_URL from the same `app.config.settings` the backend uses at
runtime, so migrations and runtime share a single source of truth for the
connection URL. `target_metadata` is `app.models.Base.metadata`, aggregating
every ORM model imported in `app.models.__init__`.

URL driver rewriting: URLs using the async driver prefix
(`postgresql+asyncpg://`) are rewritten to the sync equivalent
(`postgresql+psycopg2://`) for Alembic's sync engine. Any other URL shape
(plain `postgresql://`, SQLite, etc.) is passed through unmodified —
SQLAlchemy's default dispatch handles them.
"""

from logging.config import fileConfig

import app.models  # noqa: F401 — side-effect registers all ORM models on Base
from alembic import context

# Import settings and models before the Alembic context is used so
# target_metadata is populated before any operation triggers autogenerate.
from app.config import settings
from app.models import Base
from sqlalchemy import create_engine, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_database_url() -> str:
    """Alembic needs a sync driver even though the runtime uses asyncpg."""
    url = settings.DATABASE_URL
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_sync_database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Reflect server-side defaults/identity columns so autogen
            # doesn't churn on harmless metadata diffs.
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
