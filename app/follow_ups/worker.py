"""Background loop that fires due follow-up nudges.

Runs as a lifespan task in `app/main.py`. Every TICK seconds (default
5 minutes -- well below the smallest wait_hours = 1h so there's
plenty of resolution) it:

  1. Fetches up to BATCH_SIZE pending follow-ups whose scheduled_for
     is now-or-past.
  2. For each: checks if the user replied to the thread since the
     follow-up was created. If yes -> cancel (status='cancelled',
     reason='user_replied'). The thread is no longer stuck.
  3. Otherwise: dispatch an agent run in the same thread with a
     short seed prompt that uses the `reason` field. The agent
     reads memory + composes the nudge.
  4. Mark sent. Phase 1 fires ONCE; Phase 2 may escalate.

Failure handling per follow-up: any exception is logged and the row
stays as `pending` so the next tick retries. The worker never lets
one bad row block the rest.
"""

from __future__ import annotations

import asyncio
import uuid

import structlog
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import select

from app.agent.runner import run_agent
from app.db.models import Workspace
from app.db.session import get_session
from app.follow_ups import repository as repo
from app.slack.tokens import get_bot_token_by_workspace

log = structlog.get_logger(__name__)


# Dedicated system actor for follow-up runs. The runner skips the per-
# user memory seed for `SYSTEM_*` slack_user_ids, and Langfuse traces
# filter on this name to distinguish nudges from user-typed runs.
SYSTEM_ACTOR_SLACK_USER_ID = "SYSTEM_FOLLOWUP"

# Cadence + batching. 5 minutes is well below the min wait_hours of 1h
# so there's plenty of resolution; BATCH_SIZE 50 caps work per tick so
# a backlog after downtime drains gradually instead of spiking.
TICK_SECONDS: int = 5 * 60
BATCH_SIZE: int = 50


def _seed_prompt(reason: str) -> str:
    """The seed user-text the dispatched agent receives. Frames the
    fire as a follow-up so the agent doesn't think this is a regular
    user message.

    Kept short: the agent's own prompt has plenty of context on memory
    + tone; we just need to tell it WHAT to follow up on."""
    return (
        f"[follow-up automático] El user no respondió a tu turno anterior. "
        f"Pingéalo en una frase corta sobre lo siguiente: {reason}. "
        f"Mencioná lo que estabas esperando en lenguaje natural, "
        f"ofrecele 'si ya lo resolviste, ignorá esto'. NO repreguntes "
        f"todo de nuevo, asumí que ya tiene contexto."
    )


async def _workspace_team_id(workspace_id: uuid.UUID) -> str | None:
    """Fetch the workspace's slack_team_id. Cached at session level
    but we re-query per follow-up; workspaces are tiny and the
    follow-up volume is small."""
    async with get_session() as session:
        row = (
            await session.execute(
                select(Workspace.slack_team_id).where(Workspace.id == workspace_id)
            )
        ).scalars().first()
    return row


async def _fire_one(fu) -> None:
    """Dispatch ONE follow-up. Status transitions:
       pending -> cancelled (if user_replied_since)
       pending -> sent       (after agent run posted)
       pending -> pending    (any error; next tick retries)"""

    replied = await repo.user_replied_since(
        workspace_id=fu.workspace_id,
        app_user_id=fu.app_user_id,
        channel=fu.channel,
        conversation_key=fu.conversation_key,
        since=fu.created_at,
    )
    if replied:
        await repo.mark_cancelled(fu.id, reason="user_replied")
        log.info(
            "follow_up_auto_cancelled",
            follow_up_id=str(fu.id),
            workspace_id=str(fu.workspace_id),
            reason="user_replied",
        )
        return

    pair = await get_bot_token_by_workspace(fu.workspace_id)
    if not pair:
        log.warning(
            "follow_up_skip_no_bot_token",
            follow_up_id=str(fu.id),
            workspace_id=str(fu.workspace_id),
        )
        # Leave pending; if the workspace was uninstalled, the row
        # eventually gets cleaned up by workspace CASCADE on delete.
        # If it was a transient issue, next tick retries.
        return

    team_id = await _workspace_team_id(fu.workspace_id)
    if not team_id:
        log.warning(
            "follow_up_skip_no_team_id",
            follow_up_id=str(fu.id),
            workspace_id=str(fu.workspace_id),
        )
        return

    client = AsyncWebClient(token=pair[0])
    seed = _seed_prompt(fu.reason)
    try:
        await run_agent(
            client=client,
            team_id=team_id,
            slack_user_id=SYSTEM_ACTOR_SLACK_USER_ID,
            channel=fu.channel,
            user_text=seed,
            user_ts=fu.conversation_key,
            conversation_key=fu.conversation_key,
            reply_thread_ts=fu.reply_thread_ts,
            require_existing_thread=False,
            files=None,
            lock_handle=None,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "follow_up_dispatch_failed",
            follow_up_id=str(fu.id),
            workspace_id=str(fu.workspace_id),
            error=str(exc)[:200],
        )
        return  # leave pending; next tick will retry

    await repo.mark_sent(fu.id)
    log.info(
        "follow_up_sent",
        follow_up_id=str(fu.id),
        workspace_id=str(fu.workspace_id),
        reason=fu.reason[:80],
    )


async def tick_once() -> dict[str, int]:
    """One pass: fetch + fire due rows. Returns a count summary for
    logging. Per-row exceptions never bubble out."""
    counts = {"examined": 0, "sent": 0, "cancelled": 0, "errored": 0}
    try:
        due = await repo.fetch_due_pending(limit=BATCH_SIZE)
    except Exception as exc:  # noqa: BLE001
        log.warning("follow_up_fetch_failed", error=str(exc)[:200])
        return counts

    for fu in due:
        counts["examined"] += 1
        try:
            await _fire_one(fu)
        except Exception as exc:  # noqa: BLE001
            counts["errored"] += 1
            log.warning(
                "follow_up_fire_unexpected",
                follow_up_id=str(fu.id),
                error=str(exc)[:200],
            )
            continue
        # The repo helper sets status; we don't know which path without
        # re-querying, so the counters are approximate. Skip granularity.

    return counts


async def run_follow_up_loop(tick_seconds: int = TICK_SECONDS) -> None:
    """Background lifespan task. Sleeps `tick_seconds` between sweeps."""
    log.info("follow_up_loop_started", tick_seconds=tick_seconds)
    try:
        while True:
            try:
                counts = await tick_once()
                if counts.get("examined"):
                    log.info("follow_up_tick", **counts)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("follow_up_tick_failed", error=str(exc)[:200])
            await asyncio.sleep(tick_seconds)
    except asyncio.CancelledError:
        log.info("follow_up_loop_cancelled")
        raise


__all__ = [
    "run_follow_up_loop",
    "tick_once",
    "SYSTEM_ACTOR_SLACK_USER_ID",
    "TICK_SECONDS",
]
