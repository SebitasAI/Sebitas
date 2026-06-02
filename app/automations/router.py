"""Match events against automations and dispatch their actions.

Called by `events.consume_loop` for every published Event. Responsibilities:

1. Look up the active (non-paused) automations in the event's workspace
   with matching `trigger_type`.
2. Filter by `trigger_filter`: every key/value in the filter must match
   the event's `data` dict (subset match, key-by-key).
3. Enforce the loop guard (`MAX_FIRE_DEPTH`) -- an automation can fire a
   downstream `agent_run`, but that run's events can't fire a third
   level of automation. Without this, a misconfigured automation can
   pin one CPU and run up the Anthropic bill.
4. Open an AutomationRun row in `running`, call the action, and close
   the row with `success` / `failed`. The row persists across the
   parent automation's lifetime (FK ON DELETE SET NULL).
5. Update the parent automation row's `last_fired_at`, `last_fire_status`,
   `last_fire_error`, and `fire_count` for the future web UI.

Concurrency: this is called from a single consumer task, so the
per-automation state updates here are race-free. If we ever move to
multiple consumers, we need either per-automation locks or
`SELECT ... FOR UPDATE` on the automation row."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select

from app.automations import actions as _actions
from app.automations.events import Event
from app.db.models import Automation, AutomationRun
from app.db.session import get_session

log = structlog.get_logger(__name__)


# Loop guard: an automation can fire actions whose side effects produce
# new events (e.g. `agent_run` -> agent finishes -> emits a new event).
# `2` means: the original event can fire automations (depth 0 -> 1), and
# those automations' side effects can fire one more level (depth 1 -> 2).
# Beyond that we refuse and log -- protects against accidental loops.
MAX_FIRE_DEPTH = 2


def _filter_matches(trigger_filter: dict[str, Any], event_data: dict[str, Any]) -> bool:
    """Return True iff every key in `trigger_filter` is present in
    `event_data` with the same value. Empty filter ({}) matches anything.

    Nested values are compared with `==`, so for primitives this is the
    obvious thing; for dicts/lists it requires exact equality. Keep
    filters flat (string/number/bool keys) for the v1 UX. We deliberately
    don't support operators (>, <, contains) yet -- the agent encodes
    them by enumerating allowed values."""
    if not trigger_filter:
        return True
    for key, expected in trigger_filter.items():
        if key not in event_data:
            return False
        if event_data[key] != expected:
            return False
    return True


async def _find_matching(event: Event) -> list[Automation]:
    """All active automations in the event's workspace whose
    trigger_type matches AND whose trigger_filter is satisfied."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Automation).where(
                    Automation.workspace_id == event.workspace_id,
                    Automation.trigger_type == event.type,
                    Automation.is_paused.is_(False),
                )
            )
        ).scalars().all()
    matches = [a for a in rows if _filter_matches(a.trigger_filter, event.data)]
    return matches


async def route(event: Event) -> None:
    """Entrypoint. Find matching automations, fire each one, swallow
    per-automation failures so one bad config doesn't poison the rest."""
    if event.fire_depth > MAX_FIRE_DEPTH:
        log.warning(
            "automation_loop_guard_dropped",
            type=event.type,
            workspace_id=str(event.workspace_id),
            fire_depth=event.fire_depth,
            max_depth=MAX_FIRE_DEPTH,
        )
        return

    matches = await _find_matching(event)
    if not matches:
        return

    log.info(
        "automation_event_matched",
        type=event.type,
        workspace_id=str(event.workspace_id),
        match_count=len(matches),
        fire_depth=event.fire_depth,
    )

    for automation in matches:
        try:
            await _fire(automation, event)
        except Exception:
            log.exception(
                "automation_fire_failed",
                automation_id=str(automation.id),
                trigger_type=automation.trigger_type,
            )


async def _fire(automation: Automation, event: Event) -> None:
    """Open a run row, dispatch the action, close the row. Updates the
    parent automation row's bookkeeping fields regardless of outcome."""
    started_at = datetime.now(timezone.utc)
    async with get_session() as session:
        run = AutomationRun(
            automation_id=automation.id,
            workspace_id=automation.workspace_id,
            automation_name_snapshot=automation.name,
            trigger_event={
                "type": event.type,
                "data": event.data,
                "occurred_at": event.occurred_at.isoformat(),
                "fire_depth": event.fire_depth,
            },
            action_type=automation.action_type,
            action_config_snapshot=automation.action_config,
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
    try:
        output = await _actions.dispatch(
            automation=automation,
            event=event,
        )
    except _actions.ActionSkipped as exc:
        status = "skipped"
        error = str(exc)
        log.info(
            "automation_action_skipped",
            automation_id=str(automation.id),
            reason=str(exc),
        )
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        log.exception(
            "automation_action_error",
            automation_id=str(automation.id),
            action_type=automation.action_type,
        )

    finished_at = datetime.now(timezone.utc)
    async with get_session() as session:
        # Update the run row.
        run_obj = await session.get(AutomationRun, run_id)
        if run_obj is not None:
            run_obj.finished_at = finished_at
            run_obj.status = status
            run_obj.output = output
            run_obj.error = error
        # Update parent automation bookkeeping. Re-fetch in this session
        # because `automation` came from a different session.
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
        action_type=automation.action_type,
        status=status,
    )


__all__ = ["route", "MAX_FIRE_DEPTH"]
