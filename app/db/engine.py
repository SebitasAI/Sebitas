"""Async SQLAlchemy engine + session factory (Neon Postgres via asyncpg)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.dsn import normalize_dsn


def _build_engine() -> AsyncEngine:
    settings = get_settings()
    dsn, connect_args = normalize_dsn(settings.database_url)
    return create_async_engine(dsn, connect_args=connect_args, pool_pre_ping=True)


engine: AsyncEngine = _build_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
