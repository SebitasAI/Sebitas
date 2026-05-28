"""Per-thread serialization primitives backed by Postgres.

Goals:
- One active run per (team_id, conv_key); other threads / DMs / channels run in
  parallel total (the mutex is per-thread, NOT per-workspace).
- Multi-worker safe: the mutex lives in Postgres, not in-process. If a worker
  dies, its advisory lock auto-releases when the connection closes.
- Slack at-least-once delivery is deduped at the handler boundary by event_id.
- Messages that arrive while the mutex is held are queued; the holder drains
  on `end_turn` and coalesces them into the next run.

Two tables underlying this:
- `slack_event_seen(event_id PK, created_at)` -- INSERT ON CONFLICT DO NOTHING.
- `thread_inbox(id, team_id, conv_key, payload JSONB, created_at)` -- per-thread
  queue, drained atomically with DELETE ... RETURNING.

The lock itself is a `pg_advisory_lock` on a stable int64 hash of
`{team_id}:{conv_key}`. Session-scoped: if the worker crashes, Postgres
releases. No TTL machinery needed -- that's the whole point of advisory locks.
"""

from __future__ import annotations

import hashlib
from typing import Any

import asyncpg
import structlog
from sqlalchemy import text

from app.config import get_settings
from app.db.session import get_session

log = structlog.get_logger(__name__)


# --------------------------------------------------------------------------- #
# Lock key derivation
# --------------------------------------------------------------------------- #


def _lock_key(team_id: str, conv_key: str) -> int:
    """Stable signed int64 from (team_id, conv_key) for pg_advisory_lock."""
    digest = hashlib.blake2b(f"{team_id}:{conv_key}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def _asyncpg_dsn() -> str:
    """asyncpg doesn't accept SQLAlchemy's `+asyncpg` suffix; strip it."""
    return get_settings().database_url.replace("+asyncpg", "")


# --------------------------------------------------------------------------- #
# Thread mutex (pg_advisory_lock + dedicated connection per holder)
# --------------------------------------------------------------------------- #


class ThreadLockHandle:
    """Owned by exactly one caller; release() is idempotent. While alive the
    handle holds one asyncpg connection (cost: ~1 PG conn per active run).
    When the worker dies, the conn dies, and PG releases the lock for us."""

    __slots__ = ("_conn", "_key", "_released", "_team_id", "_conv_key")

    def __init__(self, conn: asyncpg.Connection, key: int, team_id: str, conv_key: str) -> None:
        self._conn = conn
        self._key = key
        self._released = False
        self._team_id = team_id
        self._conv_key = conv_key

    @property
    def team_id(self) -> str:
        return self._team_id

    @property
    def conv_key(self) -> str:
        return self._conv_key

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            await self._conn.execute("SELECT pg_advisory_unlock($1)", self._key)
        except Exception as exc:  # noqa: BLE001
            log.warning("advisory_unlock_failed", error=str(exc))
        try:
            await self._conn.close()
        except Exception:  # noqa: BLE001
            pass


async def try_acquire_thread_lock(team_id: str, conv_key: str) -> ThreadLockHandle | None:
    """Non-blocking. Returns a handle if the lock was acquired (caller MUST
    release on completion), None if another holder has it (caller enqueues)."""
    key = _lock_key(team_id, conv_key)
    try:
        conn = await asyncpg.connect(_asyncpg_dsn())
    except Exception as exc:  # noqa: BLE001
        log.warning("lock_connect_failed", error=str(exc))
        return None
    try:
        acquired = await conn.fetchval("SELECT pg_try_advisory_lock($1)", key)
    except Exception as exc:  # noqa: BLE001
        log.warning("lock_acquire_failed", error=str(exc))
        await conn.close()
        return None
    if not acquired:
        await conn.close()
        return None
    return ThreadLockHandle(conn, key, team_id, conv_key)


# --------------------------------------------------------------------------- #
# Slack event-id dedupe
# --------------------------------------------------------------------------- #


async def mark_event_seen(event_id: str) -> bool:
    """Returns True if this is the first sighting (caller processes), False if
    we've seen the event_id before (caller drops). Idempotent via PK."""
    if not event_id:
        return True  # no id available -> don't block
    async with get_session() as session:
        result = await session.execute(
            text("INSERT INTO slack_event_seen (event_id) VALUES (:e) ON CONFLICT DO NOTHING"),
            {"e": event_id},
        )
        await session.commit()
        return (result.rowcount or 0) > 0


async def cleanup_old_events(older_than_hours: int = 1) -> int:
    """Delete dedupe rows older than the given window. Run periodically by
    the lifespan background task in main.py."""
    async with get_session() as session:
        result = await session.execute(
            text(
                "DELETE FROM slack_event_seen "
                f"WHERE created_at < now() - interval '{int(older_than_hours)} hours'"
            )
        )
        await session.commit()
        return result.rowcount or 0


# --------------------------------------------------------------------------- #
# Per-thread inbox (queue)
# --------------------------------------------------------------------------- #


async def enqueue_message(team_id: str, conv_key: str, payload: dict[str, Any]) -> None:
    """Append a message to the thread inbox. Called when try_acquire fails."""
    async with get_session() as session:
        await session.execute(
            text(
                "INSERT INTO thread_inbox (id, team_id, conv_key, payload) "
                "VALUES (gen_random_uuid(), :t, :c, CAST(:p AS jsonb))"
            ),
            {"t": team_id, "c": conv_key, "p": _json_dumps(payload)},
        )
        await session.commit()


async def drain_inbox(team_id: str, conv_key: str) -> list[dict[str, Any]]:
    """Atomically take all queued payloads for this thread, oldest first.
    Returns the payloads; rows are deleted in the same operation."""
    async with get_session() as session:
        result = await session.execute(
            text(
                "DELETE FROM thread_inbox WHERE team_id = :t AND conv_key = :c "
                "RETURNING payload, created_at"
            ),
            {"t": team_id, "c": conv_key},
        )
        rows = result.all()
        await session.commit()
    rows = sorted(rows, key=lambda r: r[1])  # oldest first; DELETE RETURNING isn't ordered
    return [r[0] for r in rows]


# --------------------------------------------------------------------------- #


def _json_dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, default=str, ensure_ascii=False)
