"""Fire an automation from a webhook payload.

Called directly by the webhook receivers (app/automations/webhooks.py)
after they've verified the signature + resolved the automation row.
No asyncio.Queue, no matching engine, no trigger_type filters -- the
webhook URL itself already addresses one specific automation. The
router's job is mechanical:

  1. Open an `automation_run` row in status='running'.
  2. Render `prompt_template` against the payload.
  3. Invoke `run_agent` with the rendered prompt in the destination
     channel (DM with creator if NULL).
  4. Close the run row + update parent bookkeeping (last_fired_at,
     last_fire_status, fire_count).

This module also exposes a contextvar-based loop guard that the
webhook handlers may inspect. In practice, agent runs posting Slack
messages don't loop back into automations (Slack -> Misterr message
events don't fire automations; only webhooks do). Kept for future
safety in case we ever wire agent output into outbound webhooks.
"""

from __future__ import annotations

import contextvars
from datetime import datetime, timezone
from typing import Any

import structlog

from app.automations import actions as _actions
from app.db.models import Automation, AutomationRun
from app.db.session import get_session

log = structlog.get_logger(__name__)


# Max consecutive fires from inside a single webhook handling chain.
# Almost impossible to hit today (webhooks come from outside; agent
# runs post to Slack, which doesn't loop back). Kept as a defense in
# depth in case a future outbound-webhook tool lets automations fire
# each other.
MAX_FIRE_DEPTH = 2

_fire_depth_var: contextvars.ContextVar[int] = contextvars.ContextVar(
    "automation_fire_depth", default=0
)


def current_fire_depth() -> int:
    return _fire_depth_var.get()


def _push_depth(d: int) -> contextvars.Token[int]:
    return _fire_depth_var.set(d)


def _pop_depth(token: contextvars.Token[int]) -> None:
    _fire_depth_var.reset(token)


class LoopGuardDropped(Exception):
    """Raised when a fire is refused because the call stack already
    exceeds MAX_FIRE_DEPTH. Webhook handlers should log + 200 OK."""


async def fire(
    automation: Automation,
    payload: dict[str, Any],
) -> None:
    """Single entry point used by the webhook receivers. Best-effort:
    catches everything internally and writes status to the run row;
    never re-raises to the webhook handler (which must always return
    2xx so upstream providers don't retry the same event).

    Returns nothing -- the caller already returned 2xx by the time
    `_fire_inner` finishes (we expect the webhook handler to spawn
    this on `asyncio.create_task`)."""
    depth = current_fire_depth()
    if depth > MAX_FIRE_DEPTH:
        log.warning(
            "automation_loop_guard_dropped",
            automation_id=str(automation.id),
            depth=depth,
            max_depth=MAX_FIRE_DEPTH,
        )
        return

    started_at = datetime.now(timezone.utc)
    # Open the run row in 'running' BEFORE invoking the action, so a
    # crash mid-fire still leaves a trace.
    async with get_session() as session:
        run = AutomationRun(
            automation_id=automation.id,
            workspace_id=automation.workspace_id,
            automation_name_snapshot=automation.name,
            trigger_payload=payload,
            prompt_template_snapshot=automation.prompt_template,
            started_at=started_at,
            status="running",
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        run_id = run.id

    output: str | None = None
    error: str | None = None
    status = "success"
    rendered: str | None = None
    token = _push_depth(depth + 1)
    try:
        rendered, output = await _actions.fire_agent_run(automation, payload)
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        log.exception(
            "automation_fire_failed",
            automation_id=str(automation.id),
            source=automation.source,
        )
    finally:
        _pop_depth(token)

    finished_at = datetime.now(timezone.utc)
    async with get_session() as session:
        run_obj = await session.get(AutomationRun, run_id)
        if run_obj is not None:
            run_obj.finished_at = finished_at
            run_obj.status = status
            run_obj.output = output
            run_obj.error = error
            run_obj.rendered_prompt = rendered
        parent = await session.get(Automation, automation.id)
        if parent is not None:
            parent.last_fired_at = finished_at
            parent.last_fire_status = status
            parent.last_fire_error = error
            parent.fire_count = (parent.fire_count or 0) + 1
        await session.commit()

    log.info(
        "automation_fired",
        automation_id=str(automation.id),
        run_id=str(run_id),
        source=automation.source,
        status=status,
    )


__all__ = ["fire", "MAX_FIRE_DEPTH", "current_fire_depth", "LoopGuardDropped"]
