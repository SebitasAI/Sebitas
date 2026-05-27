"""Async-aware Alembic environment.

Only depends on DATABASE_URL (via app.db.dsn) and the model metadata, so running
migrations does not require the full application settings (Slack/Anthropic keys).
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# Make the project root importable regardless of how alembic is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.dsn import normalize_dsn  # noqa: E402
from app.db.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _dsn() -> tuple[str, dict]:
    url = os.environ["DATABASE_URL"]
    return normalize_dsn(url)


def run_migrations_offline() -> None:
    dsn, _ = _dsn()
    context.configure(
        url=dsn,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:  # noqa: ANN001
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    dsn, connect_args = _dsn()
    engine = create_async_engine(dsn, connect_args=connect_args)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
