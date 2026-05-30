"""Background scheduler for scheduled tasks (slice T-1).

Runs as a lifespan task in `app/main.py` -- one asyncio loop in the API
process, same pattern as `cleanup_task` / `roster_task` etc. Cross-instance
safety comes from `SELECT ... FOR UPDATE SKIP LOCKED` on the claim, not from
an extra advisory lock (redundant inside a single tx).

Each tick (30s):
  1. Open one DB transaction, claim up to 100 due rows.
  2. For each, advance the row's state: bump last_run_at, set
     last_run_status='running', recompute next_run_at from now() (skip-not-
     catchup: missed fires are logged with the count, not executed).
  3. Commit; the rows are released and persisted with the new next_run_at.
  4. Dispatch each fire as a detached asyncio task -- they call the agent
     runner, which is the same code path Slack messages use. Doing this
     after commit means a tick failure can't double-fire a row that was
     already dispatched, and tasks that take minutes don't block the next
     30s tick.

System actor: scheduled fires need a `slack_user_id` for the runner's
`upsert_app_user` call. We use a per-workspace sentinel AppUser (created
lazily on first fire via the existing upsert path). The sentinel id starts
with 'SYSTEM_' so it can't collide with real Slack user ids (UXXXXXX).

Destination NULL handling: a system task whose workspace has no
bot_home_channel_id yet is marked failed (with a clear error message) and
its next_run_at is advanced normally. No auto-pause -- once admin sets the
channel, the next tick picks the task up and it works.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import structlog
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import desc, select

from app.agent.runner import run_agent
from app.db.models import Message, ScheduledTask, Thread, Workspace
from app.db.session import get_session
from app.scheduled_tasks import repository as repo
from app.slack.crypto import TokenCryptoError, decrypt_token

log = structlog.get_logger(__name__)


# Slack ids for real users / channels start with U/C/D/G/T. The 'SYSTEM_'
# prefix guarantees no clash. Stored as AppUser.slack_user_id; the column
# is String(32) so we stay well under.
SYSTEM_ACTOR_SLACK_USER_ID = "SYSTEM_SCHEDULED"

# Tick interval. Anything finer than this is wasted work (the cron-cadence
# floor is 5 minutes -- see repository.MIN_CRON_INTERVAL_S); anything coarser
# loses sub-minute accuracy on user-visible schedules like "9:00 AM".
SCHEDULER_TICK_SECONDS = 30

# How many rows the scheduler grabs per tick. The query is partial-indexed
# on next_run_at <= now() so the upper bound is "at most this many tasks
# were due in the last tick window across the whole tenant base." 100 is
# generous; raise if we ever bump up against it.
SCHEDULER_CLAIM_LIMIT = 100


@dataclass(frozen=True)
class _PendingFire:
    """Snapshot of a task at claim time. Captured BEFORE the agent runs so
    the dispatcher has stable values even after `record_fire_started` mutates
    the row (and so we can pass `last_run_summary` from the previous run as
    context for the new run)."""

    task_id: uuid.UUID
    workspace_id: uuid.UUID
    scope: str
    name: str
    prompt: str
    destination_type: str
    destination_slack_id: str
    cron_spec: str
    timezone: str
    previous_summary: str | None
    fire_once: bool
    prompt_is_literal: bool


# --------------------------------------------------------------------------- #
# Tick: claim + advance state + dispatch
# --------------------------------------------------------------------------- #


async def _tick(now_utc: datetime | None = None) -> int:
    """One scheduler iteration. Returns the number of fires dispatched. Any
    exception inside is logged + swallowed by `run_scheduler_loop`; tick
    failures must not kill the loop."""
    now_utc = now_utc or datetime.now(timezone.utc)
    pending: list[_PendingFire] = []
    skipped_missing_destination: list[uuid.UUID] = []

    async with get_session() as session:
        due = await repo.claim_due_tasks(
            session, limit=SCHEDULER_CLAIM_LIMIT, now_utc=now_utc
        )
        if not due:
            return 0

        for task in due:
            # System tasks without a configured home channel can't fire.
            # Mark failed and advance next_run_at so the row keeps trying;
            # a later admin action to set bot_home_channel_id recovers it.
            if not task.destination_slack_id:
                task.last_run_at = now_utc
                task.last_run_status = "failed"
                task.last_run_error = (
                    "no destination_slack_id configured "
                    "(for system tasks this means workspace.bot_home_channel_id is null)"
                )
                task.next_run_at = repo.compute_next_run_at(
                    task.cron_spec, task.timezone, base_utc=now_utc
                )
                skipped_missing_destination.append(task.id)
                await session.flush()
                continue

            missed = await repo.record_fire_started(session, task, now_utc=now_utc)
            if missed:
                log.info(
                    "scheduled_task_skipped_missed_fires",
                    task_id=str(task.id),
                    workspace_id=str(task.workspace_id),
                    missed_count=missed,
                    last_fire=task.last_run_at.isoformat() if task.last_run_at else None,
                    now=now_utc.isoformat(),
                )
            log.info(
                "scheduled_task_fired",
                task_id=str(task.id),
                workspace_id=str(task.workspace_id),
                scope=task.scope,
                name=task.name,
                actual_fire_time=now_utc.isoformat(),
                next_run_at=task.next_run_at.isoformat() if task.next_run_at else None,
                timezone=task.timezone,
            )
            pending.append(
                _PendingFire(
                    task_id=task.id,
                    workspace_id=task.workspace_id,
                    scope=task.scope,
                    name=task.name,
                    prompt=task.prompt,
                    destination_type=task.destination_type,
                    destination_slack_id=task.destination_slack_id,
                    cron_spec=task.cron_spec,
                    timezone=task.timezone,
                    previous_summary=task.last_run_summary,
                    fire_once=task.fire_once,
                    prompt_is_literal=task.prompt_is_literal,
                )
            )

        # Commit BEFORE dispatching anything: a row-state failure must not
        # leak into an async agent invocation that the DB hasn't recorded.
        await session.commit()

    for task_id in skipped_missing_destination:
        log.warning(
            "scheduled_task_missing_destination",
            task_id=str(task_id),
        )

    for fire in pending:
        # Detached: the scheduler loop must keep ticking even when individual
        # runs take minutes. Failures are caught + recorded inside _dispatch_fire.
        asyncio.create_task(_dispatch_fire(fire))

    return len(pending)


# --------------------------------------------------------------------------- #
# Dispatch: open Slack thread + run the agent + persist outcome
# --------------------------------------------------------------------------- #


async def _dispatch_fire(fire: _PendingFire) -> None:
    """Execute a single fire end-to-end. Always lands on `record_fire_finished`
    so the row reflects the outcome even when the agent run dies."""
    start = time.monotonic()
    status: str = "failed"
    summary: str | None = None
    error: str | None = None
    try:
        summary, error = await _execute_fire(fire)
        status = "success" if error is None else "failed"
    except Exception as exc:  # noqa: BLE001
        # The runner has its own user-facing error handler; if we land here,
        # something deeper broke (e.g. token decrypt failure).
        log.warning(
            "scheduled_task_dispatch_failed",
            task_id=str(fire.task_id),
            error=str(exc)[:500],
        )
        status = "failed"
        error = str(exc)[: repo.MAX_LAST_RUN_ERROR_LEN]
    finally:
        try:
            await repo.record_fire_finished(
                fire.task_id, status=status, summary=summary, error=error
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "scheduled_task_record_finish_failed",
                task_id=str(fire.task_id),
                error=str(exc)[:500],
            )
        log.info(
            "scheduled_task_completed",
            task_id=str(fire.task_id),
            workspace_id=str(fire.workspace_id),
            status=status,
            duration_seconds=round(time.monotonic() - start, 3),
            error=error,
        )


async def _execute_fire(fire: _PendingFire) -> tuple[str | None, str | None]:
    """Open the destination thread and invoke the agent. Returns
    (summary, error_msg); error_msg is None on success."""
    async with get_session() as session:
        ws = (
            await session.execute(
                select(Workspace).where(Workspace.id == fire.workspace_id)
            )
        ).scalar_one_or_none()
    if ws is None:
        return None, "workspace row gone"
    if not ws.bot_token:
        return None, "workspace has no bot_token (not installed)"
    try:
        bot_token = decrypt_token(ws.bot_token)
    except TokenCryptoError as exc:
        return None, f"could not decrypt bot_token: {exc}"

    client = AsyncWebClient(token=bot_token)

    # Resolve destination -> channel id we can post to. For DM destinations,
    # `conversations.open(users=UXXX)` yields the DXXX channel; for channel
    # destinations the id is already what we need.
    try:
        channel_id = await _resolve_post_channel(
            client, fire.destination_type, fire.destination_slack_id
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"could not resolve destination: {exc}"[: repo.MAX_LAST_RUN_ERROR_LEN]

    # Two orthogonal questions decide the fire flow:
    #   1. Does the agent run? -> prompt_is_literal flips this.
    #   2. Is it deleted after one fire? -> fire_once flips this (handled
    #      in record_fire_finished, not here).
    #
    # Combinations:
    #   - prompt_is_literal=False, fire_once=False -> recurring agentic
    #     (daily-brief, workflow-discovery). Parent + thread + agent.
    #   - prompt_is_literal=False, fire_once=True  -> one-shot agentic
    #     ("en 2 min revisame el chat y avisame"). Parent + thread + agent,
    #     row deleted on fire.
    #   - prompt_is_literal=True,  fire_once=True  -> one-shot literal
    #     (send_delayed_message). No agent, post text verbatim.
    #   - prompt_is_literal=True,  fire_once=False -> not currently exposed
    #     in any tool but handled identically to the third case at fire
    #     time. The row just doesn't get deleted.
    if fire.prompt_is_literal:
        try:
            await client.chat_postMessage(channel=channel_id, text=fire.prompt)
        except Exception as exc:  # noqa: BLE001
            return None, f"chat.postMessage failed: {exc}"[: repo.MAX_LAST_RUN_ERROR_LEN]
        return None, None

    # Agentic path (recurring OR one-shot): parent + thread + run_agent.
    parent_text = f":alarm_clock: Scheduled task `{fire.name}`"
    try:
        post_resp = await client.chat_postMessage(channel=channel_id, text=parent_text)
        parent_ts = post_resp.get("ts") if isinstance(post_resp, dict) else post_resp["ts"]
    except Exception as exc:  # noqa: BLE001
        return None, f"could not post parent message: {exc}"[: repo.MAX_LAST_RUN_ERROR_LEN]
    if not parent_ts:
        return None, "Slack returned no ts for parent message"

    seed_text = _build_seed_text(fire)

    try:
        await run_agent(
            client=client,
            team_id=ws.slack_team_id,
            slack_user_id=SYSTEM_ACTOR_SLACK_USER_ID,
            channel=channel_id,
            user_text=seed_text,
            user_ts=parent_ts,
            conversation_key=parent_ts,
            reply_thread_ts=parent_ts,
            require_existing_thread=False,
            files=None,
            lock_handle=None,
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"agent run errored: {exc}"[: repo.MAX_LAST_RUN_ERROR_LEN]

    # Don't bother fetching a summary for one-shot agentic rows -- they get
    # deleted on fire so no future run consumes it.
    if fire.fire_once:
        return None, None
    summary = await _fetch_latest_assistant_text(
        ws.id, channel_id, parent_ts
    )
    return summary, None


async def _resolve_post_channel(
    client: AsyncWebClient, destination_type: str, destination_slack_id: str
) -> str:
    """Return the channel id `chat.postMessage` should target.

    Slack's chat.postMessage accepts a Slack user id (UXXX) directly as the
    `channel` argument and opens the DM channel server-side -- no need for
    a pre-call to conversations.open, which would require the `im:write`
    scope that Misterr's bot doesn't currently request. For both DM and
    channel destinations we just pass through the stored id.

    Kept as a function (rather than inlined) so future tweaks (e.g.
    name-to-id resolution for `#channel` literals) live in one place."""
    return destination_slack_id


def _build_seed_text(fire: _PendingFire) -> str:
    """User-message body fed to the agent runner. The leading bracket block
    is metadata for the agent (task name + previous summary); the actual
    `prompt` follows after the divider."""
    prev_line = (
        fire.previous_summary.strip()
        if fire.previous_summary
        else "no prior summary"
    )
    if len(prev_line) > 1800:
        prev_line = prev_line[:1800].rstrip() + "…"
    return (
        "[Scheduled task context]\n"
        f"Task name: {fire.name}\n"
        f"Scope: {fire.scope}\n"
        f"Cron: {fire.cron_spec} ({fire.timezone})\n"
        f"Last run summary: {prev_line}\n"
        "---\n"
        f"{fire.prompt}"
    )


async def _fetch_latest_assistant_text(
    workspace_id: uuid.UUID, channel_id: str, thread_ts: str
) -> str | None:
    """Return the most recent assistant message text in the scheduled-task
    thread, or None if nothing was persisted. Used to populate
    last_run_summary for the next fire."""
    async with get_session() as session:
        thread = (
            await session.execute(
                select(Thread).where(
                    Thread.workspace_id == workspace_id,
                    Thread.slack_channel_id == channel_id,
                    Thread.slack_thread_ts == thread_ts,
                )
            )
        ).scalar_one_or_none()
        if thread is None:
            return None
        msg = (
            await session.execute(
                select(Message.text)
                .where(Message.thread_id == thread.id, Message.role == "assistant")
                .order_by(desc(Message.created_at))
                .limit(1)
            )
        ).scalar_one_or_none()
    if not msg:
        return None
    # Truncation happens again inside record_fire_finished, but trim here so
    # the structlog payload stays bounded too.
    if len(msg) > repo.MAX_LAST_RUN_SUMMARY_LEN:
        msg = msg[: repo.MAX_LAST_RUN_SUMMARY_LEN].rstrip() + "…"
    return msg


# --------------------------------------------------------------------------- #
# Entry point (mounted from app/main.py lifespan)
# --------------------------------------------------------------------------- #


async def run_scheduler_loop() -> None:
    """Forever loop. Cancellation (via asyncio.CancelledError on shutdown) is
    the only clean exit. Tick exceptions are logged + swallowed; we never
    want a transient DB blip to kill the loop."""
    log.info("scheduled_task_scheduler_started", tick_seconds=SCHEDULER_TICK_SECONDS)
    while True:
        try:
            fired = await _tick()
            if fired:
                log.info("scheduled_task_tick_summary", fired=fired)
        except asyncio.CancelledError:
            log.info("scheduled_task_scheduler_cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("scheduled_task_tick_failed", error=str(exc)[:500])
        try:
            await asyncio.sleep(SCHEDULER_TICK_SECONDS)
        except asyncio.CancelledError:
            log.info("scheduled_task_scheduler_cancelled")
            raise


__all__ = [
    "SYSTEM_ACTOR_SLACK_USER_ID",
    "SCHEDULER_TICK_SECONDS",
    "SCHEDULER_CLAIM_LIMIT",
    "run_scheduler_loop",
]


# Internal symbols exposed for testing (renamed with leading underscore so
# they don't pollute the public API surface).
_tick_for_test = _tick
_dispatch_fire_for_test = _dispatch_fire
_execute_fire_for_test = _execute_fire
_build_seed_text_for_test = _build_seed_text
