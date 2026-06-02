"""Auto-nudge for integration connect flows that the user started and
never finished (Phase 2).

The flow: a user asks Misterr to do something that needs Salesforce.
Misterr posts a "Connect Salesforce" button and parks an
`IntegrationConnection` row with `status='pending'`. If the user
clicks through and completes OAuth, the row flips to `'connected'`.
If they don't click (or start the OAuth and bail mid-flow), the row
sits at `'pending'` indefinitely while the agent waits.

This sweeper turns those zombies into follow-ups: if a row has been
`'pending'` for more than `STALE_AFTER_HOURS`, we agenda a nudge into
the thread where the connect was triggered.

Dedup: we mint `created_by_run_id = "integration:{connection_id}"` so
the follow_up repo's existing dedup-by-run-id logic prevents creating
multiple nudges for the same stuck connection. Subsequent sweeper
ticks see the existing row and skip.

We do NOT delete the IntegrationConnection rows here -- the follow-up
keeps the user informed; deletion is a user-facing decision (or a
separate housekeeping sweeper).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select

from app.db.models import AppUser, IntegrationConnection
from app.db.session import get_session
from app.follow_ups import repository as fu_repo

log = structlog.get_logger(__name__)


# When a pending integration counts as "stale" enough to nudge.
# Short enough that the user remembers what they were doing, long
# enough that the legitimate OAuth click-through path doesn't get
# nudged mid-flow.
STALE_AFTER_HOURS: int = 4

# How long until the worker fires the nudge (added on top of the
# IntegrationConnection's age). 0 means: nudge on the next worker tick.
NUDGE_DELAY_HOURS: int = 0

# Cadence of this sweeper. Runs alongside the main follow_up worker
# but on a separate loop so a slow integration query doesn't block the
# regular per-row dispatch.
SWEEP_INTERVAL_SECONDS: int = 30 * 60  # 30 min


async def _resolve_app_user_id(
    workspace_id: uuid.UUID, slack_user_id: str | None
) -> uuid.UUID | None:
    """Look up the AppUser for a given slack_user_id. Returns None
    when we can't resolve -- caller skips that connection."""
    if not slack_user_id:
        return None
    async with get_session() as session:
        return (
            await session.execute(
                select(AppUser.id).where(
                    AppUser.workspace_id == workspace_id,
                    AppUser.slack_user_id == slack_user_id,
                )
            )
        ).scalars().first()


async def _stale_pending_integrations() -> list[IntegrationConnection]:
    """Return IntegrationConnection rows that look stuck. Criteria:

      - status = 'pending'
      - pending_ctx IS NOT NULL (without it we don't know where to post)
      - created_at older than STALE_AFTER_HOURS

    Cap at 200 rows per sweep so a backlog doesn't spam the worker
    in a single tick."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=STALE_AFTER_HOURS)
    async with get_session() as session:
        rows = (
            await session.execute(
                select(IntegrationConnection)
                .where(
                    IntegrationConnection.status == "pending",
                    IntegrationConnection.pending_ctx.isnot(None),
                    IntegrationConnection.created_at < cutoff,
                )
                .order_by(IntegrationConnection.created_at.asc())
                .limit(200)
            )
        ).scalars().all()
    return list(rows)


def _conversation_key_from_ctx(ctx: dict) -> str | None:
    """The pending ctx is whatever the agent runner stored when the
    connect interrupt fired. Different runner paths put the thread id
    in slightly different fields; cover the known shapes."""
    if not isinstance(ctx, dict):
        return None
    return (
        ctx.get("conversation_key")
        or ctx.get("reply_thread_ts")
        or ctx.get("user_ts")
    )


async def sweep_once() -> dict[str, int]:
    """Single pass over stale pending integrations. Returns a tiny
    summary for logging. Per-row failures are logged + skipped."""
    counts = {"examined": 0, "scheduled": 0, "skipped": 0}
    try:
        stale = await _stale_pending_integrations()
    except Exception as exc:  # noqa: BLE001
        log.warning("integration_sweeper_query_failed", error=str(exc)[:200])
        return counts

    for conn in stale:
        counts["examined"] += 1
        ctx = conn.pending_ctx or {}
        channel = ctx.get("channel")
        conversation_key = _conversation_key_from_ctx(ctx)
        slack_user_id = ctx.get("slack_user_id")
        if not (channel and conversation_key and slack_user_id):
            log.info(
                "integration_sweep_skipped_missing_ctx",
                connection_id=str(conn.id),
                app=conn.app,
            )
            counts["skipped"] += 1
            continue

        app_user_id = await _resolve_app_user_id(conn.workspace_id, slack_user_id)
        if app_user_id is None:
            counts["skipped"] += 1
            continue

        dedup_key = f"integration:{conn.id}"
        reason = (
            f"conexión con {conn.app} quedó a medias hace unas horas; "
            f"preguntale al user si querés terminar el OAuth"
        )
        new_id = await fu_repo.create_follow_up(
            workspace_id=conn.workspace_id,
            app_user_id=app_user_id,
            channel=channel,
            conversation_key=conversation_key,
            reply_thread_ts=ctx.get("reply_thread_ts"),
            reason=reason,
            wait_hours=max(fu_repo.MIN_WAIT_HOURS, NUDGE_DELAY_HOURS),
            created_by_run_id=dedup_key,
        )
        if new_id is None:
            # Most common cause: the dedup hit (a previous sweep
            # already created a follow-up for this connection that's
            # still pending). That's the intended idempotency.
            counts["skipped"] += 1
            continue
        counts["scheduled"] += 1
        log.info(
            "integration_sweep_scheduled_followup",
            follow_up_id=str(new_id),
            connection_id=str(conn.id),
            app=conn.app,
            workspace_id=str(conn.workspace_id),
        )
    return counts


async def run_integration_sweep_loop(
    interval_seconds: int = SWEEP_INTERVAL_SECONDS,
) -> None:
    """Background lifespan task. Sleeps `interval_seconds` between
    sweeps; per-tick errors logged but never bubble out."""
    log.info("integration_sweep_loop_started", interval_seconds=interval_seconds)
    try:
        while True:
            try:
                counts = await sweep_once()
                if counts.get("scheduled"):
                    log.info("integration_sweep_done", **counts)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("integration_sweep_tick_failed", error=str(exc)[:200])
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        log.info("integration_sweep_loop_cancelled")
        raise


__all__ = [
    "sweep_once",
    "run_integration_sweep_loop",
    "STALE_AFTER_HOURS",
    "SWEEP_INTERVAL_SECONDS",
]
