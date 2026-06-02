"""In-process pub/sub for automation triggers.

Publishers (the agent runner, the LangGraph tool node, the Slack feedback
handler, the scheduled-task scheduler) call `publish()` with an Event.
A single `consume_loop()` background task picks events off the asyncio
queue and hands each one to the router.

Why in-process and not Postgres LISTEN/NOTIFY or Redis Streams:

- We run one app process per Render service. Cross-instance fan-out is
  not needed yet -- when we scale horizontally, we swap this module for
  a Postgres/Redis-backed queue without touching publishers (they keep
  calling `publish()`).
- asyncio.Queue is lossy on process crash. That's fine for automations:
  they react to live signals, not authoritative state. A missed
  `agent_error` event during a restart is acceptable; the next one will
  fire normally. If we ever need durability, this is the seam.

Backpressure: queue is unbounded. The consumer is a single coroutine
that processes events serially; if a single fire takes 30s (e.g. an
`agent_run` action), every subsequent event waits. That's intentional
for v1 -- it keeps loop-guard accounting linear and avoids piling
concurrent agent invocations on the same workspace. If throughput
becomes the bottleneck, fan out per-workspace queues."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Event:
    """A trigger candidate. Routed by `type`, filtered by `data`.

    `type` matches the automation.trigger_type CHECK constraint values.
    `data` is the payload publishers attach -- the automation's
    `trigger_filter` (a dict) is matched key-by-key against this, so
    keep keys flat and primitive (strings, numbers, bools). Nested
    structures still work but can't be filtered on.

    `workspace_id` is mandatory: every automation is workspace-scoped,
    and the router will only consider automations in the event's
    workspace. This is the multi-tenant fail-safe -- never publish an
    Event without it.

    `fire_depth` tracks recursion for the loop guard. When an
    `agent_run` action triggers a new agent run, that run's events
    inherit `fire_depth + 1`. The router refuses to fire when depth
    exceeds the configured maximum (see router.MAX_FIRE_DEPTH)."""

    type: str
    workspace_id: UUID
    data: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fire_depth: int = 0


# Contextvar set by the `agent_run` action dispatcher before kicking off
# a downstream agent run. Any event published from inside that run will
# read this value via `current_fire_depth()` and stamp it on the Event
# so the router can enforce the loop guard. Default 0 means "not inside
# an automation-triggered run".
_fire_depth_var: contextvars.ContextVar[int] = contextvars.ContextVar(
    "automation_fire_depth", default=0
)


def current_fire_depth() -> int:
    """Return the inherited fire depth for the current asyncio context.
    Publishers call this to stamp their Event. Returns 0 outside an
    automation-triggered code path."""
    return _fire_depth_var.get()


def set_fire_depth(depth: int) -> contextvars.Token[int]:
    """Set the fire depth for the current context. Caller must
    `_fire_depth_var.reset(token)` once the downstream work completes.
    Used by the `agent_run` action dispatcher."""
    return _fire_depth_var.set(depth)


def reset_fire_depth(token: contextvars.Token[int]) -> None:
    _fire_depth_var.reset(token)


# Single process-wide queue. Created lazily so importing this module
# during alembic / one-off scripts doesn't allocate an event loop ref.
_queue: asyncio.Queue[Event] | None = None
_consumer_task: asyncio.Task[None] | None = None


def _get_queue() -> asyncio.Queue[Event]:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


async def publish(event: Event) -> None:
    """Publish an event. Never raises -- a failed publish must not
    take down the caller (agent run, Slack handler, scheduler). Worst
    case we log and the automation simply doesn't fire."""
    try:
        _get_queue().put_nowait(event)
        log.debug(
            "automation_event_published",
            type=event.type,
            workspace_id=str(event.workspace_id),
            fire_depth=event.fire_depth,
        )
    except Exception:
        # Unbounded queue -> only realistic failure is event-loop
        # weirdness during shutdown. Swallow loudly.
        log.exception("automation_event_publish_failed", type=event.type)


async def consume_loop() -> None:
    """Background coroutine. Started once per process via FastAPI
    lifespan. Pulls events off the queue and routes them. Cancellation
    is the supported stop signal -- the lifespan handler cancels this
    task on shutdown."""
    # Local import: router imports back into events for the Event type,
    # so defer to break the cycle.
    from app.automations import router as _router  # noqa: PLC0415

    log.info("automation_consume_loop_started")
    queue = _get_queue()
    try:
        while True:
            event = await queue.get()
            try:
                await _router.route(event)
            except Exception:
                # One bad event must not kill the loop. Log and move on.
                log.exception(
                    "automation_event_route_failed",
                    type=event.type,
                    workspace_id=str(event.workspace_id),
                )
            finally:
                queue.task_done()
    except asyncio.CancelledError:
        log.info("automation_consume_loop_stopping")
        raise


def start_consumer() -> asyncio.Task[None]:
    """Spawn the consumer task. Idempotent: if already running, returns
    the existing task. Lifespan handler calls this on startup."""
    global _consumer_task
    if _consumer_task is not None and not _consumer_task.done():
        return _consumer_task
    _consumer_task = asyncio.create_task(consume_loop(), name="automation_consumer")
    return _consumer_task


async def stop_consumer() -> None:
    """Cancel the consumer and wait for it to wind down. Safe to call
    even if never started."""
    global _consumer_task
    if _consumer_task is None:
        return
    _consumer_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _consumer_task
    _consumer_task = None


__all__ = [
    "Event",
    "publish",
    "consume_loop",
    "start_consumer",
    "stop_consumer",
    "current_fire_depth",
    "set_fire_depth",
    "reset_fire_depth",
]
