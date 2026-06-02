"""CRUD for follow_up rows.

Thin layer over SQLAlchemy. Callers (the agent tool + the worker) get a
narrow surface so we don't end up with raw queries spread across the
codebase. All helpers return UUIDs or count summaries; raw model
instances stay scoped to repository internals so the schema can evolve
without touching every callsite.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import select, update

from app.db.models import FollowUp, Message, Thread
from app.db.session import get_session

log = structlog.get_logger(__name__)


# Phase 1 limits. Tight so the agent can't accidentally schedule 50
# nudges in one turn or wait years to fire.
MIN_WAIT_HOURS: int = 1
MAX_WAIT_HOURS: int = 7 * 24  # one week
MAX_PER_RUN: int = 1  # the agent should only ever schedule one per turn


async def create_follow_up(
    *,
    workspace_id: uuid.UUID,
    app_user_id: uuid.UUID,
    channel: str,
    conversation_key: str,
    reply_thread_ts: str | None,
    reason: str,
    wait_hours: int,
    created_by_run_id: str | None = None,
) -> uuid.UUID | None:
    """Insert a pending follow-up. Returns the new id on success, None
    on validation failure (reason empty, wait out of range). The agent
    tool layer translates None into a user-visible error.

    Idempotency: if `created_by_run_id` matches an existing follow_up,
    we return the existing id rather than insert a duplicate. This
    catches the case where the agent calls the tool twice in one turn
    (e.g. retry after a transient error). Phase 1 caps to MAX_PER_RUN
    per `created_by_run_id`.
    """
    reason = (reason or "").strip()
    if not reason:
        return None
    if not (MIN_WAIT_HOURS <= wait_hours <= MAX_WAIT_HOURS):
        return None

    scheduled_for = datetime.now(timezone.utc) + timedelta(hours=wait_hours)

    async with get_session() as session:
        # Dedup per-run: if this run already opened one, return that.
        if created_by_run_id:
            existing = (
                await session.execute(
                    select(FollowUp.id).where(
                        FollowUp.created_by_run_id == created_by_run_id,
                        FollowUp.status == "pending",
                    )
                )
            ).scalars().first()
            if existing is not None:
                return existing

        row = FollowUp(
            workspace_id=workspace_id,
            app_user_id=app_user_id,
            channel=channel,
            conversation_key=conversation_key,
            reply_thread_ts=reply_thread_ts,
            reason=reason,
            scheduled_for=scheduled_for,
            status="pending",
            nudge_count=0,
            created_by_run_id=created_by_run_id,
        )
        session.add(row)
        await session.commit()
        new_id = row.id

    log.info(
        "follow_up_created",
        follow_up_id=str(new_id),
        workspace_id=str(workspace_id),
        app_user_id=str(app_user_id),
        wait_hours=wait_hours,
        run_id=created_by_run_id,
    )
    return new_id


async def fetch_due_pending(*, limit: int = 50) -> list[FollowUp]:
    """Return up to `limit` pending follow-ups whose scheduled_for is
    now-or-past. Ordered oldest-due first so the worker drains FIFO."""
    now = datetime.now(timezone.utc)
    async with get_session() as session:
        rows = (
            await session.execute(
                select(FollowUp)
                .where(
                    FollowUp.status == "pending",
                    FollowUp.scheduled_for <= now,
                )
                .order_by(FollowUp.scheduled_for.asc())
                .limit(limit)
            )
        ).scalars().all()
    return list(rows)


async def mark_sent(follow_up_id: uuid.UUID) -> None:
    async with get_session() as session:
        await session.execute(
            update(FollowUp)
            .where(FollowUp.id == follow_up_id)
            .values(
                status="sent",
                sent_at=datetime.now(timezone.utc),
                nudge_count=FollowUp.nudge_count + 1,
            )
        )
        await session.commit()


async def mark_cancelled(follow_up_id: uuid.UUID, *, reason: str) -> None:
    async with get_session() as session:
        await session.execute(
            update(FollowUp)
            .where(FollowUp.id == follow_up_id, FollowUp.status == "pending")
            .values(
                status="cancelled",
                cancelled_at=datetime.now(timezone.utc),
                cancelled_reason=reason[:32],
            )
        )
        await session.commit()


async def user_replied_since(
    *,
    workspace_id: uuid.UUID,
    app_user_id: uuid.UUID,
    channel: str,
    conversation_key: str,
    since: datetime,
) -> bool:
    """Return True iff there's at least one `role='user'` message from
    `app_user_id` in the thread identified by (workspace_id, channel,
    conversation_key) with created_at > `since`.

    `conversation_key` is the value the runner uses internally; it maps
    to `Thread.slack_thread_ts` (the per-thread Slack timestamp, or the
    channel id for top-level DMs). The agent runner has the same dual
    use, so we keep parameter names consistent."""
    async with get_session() as session:
        thread_id = (
            await session.execute(
                select(Thread.id).where(
                    Thread.workspace_id == workspace_id,
                    Thread.slack_channel_id == channel,
                    Thread.slack_thread_ts == conversation_key,
                )
            )
        ).scalars().first()
        if thread_id is None:
            return False
        row = (
            await session.execute(
                select(Message.id)
                .where(
                    Message.thread_id == thread_id,
                    Message.role == "user",
                    Message.app_user_id == app_user_id,
                    Message.created_at > since,
                )
                .limit(1)
            )
        ).scalars().first()
        return row is not None


async def follow_up_summary_for_user(
    *, workspace_id: uuid.UUID, app_user_id: uuid.UUID
) -> dict[str, Any]:
    """Stats the agent / admin can surface back. Cheap aggregate query.
    Not used by the worker; exposed for completeness."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(FollowUp.status).where(
                    FollowUp.workspace_id == workspace_id,
                    FollowUp.app_user_id == app_user_id,
                )
            )
        ).scalars().all()
    summary = {"pending": 0, "sent": 0, "cancelled": 0}
    for s in rows:
        if s in summary:
            summary[s] += 1
    return summary


__all__ = [
    "create_follow_up",
    "fetch_due_pending",
    "mark_sent",
    "mark_cancelled",
    "user_replied_since",
    "follow_up_summary_for_user",
    "MIN_WAIT_HOURS",
    "MAX_WAIT_HOURS",
]
