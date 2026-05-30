"""REST endpoints for the Misterr web app's Scheduled Tasks page (slice T-2).

The web only reads + pauses/resumes; create/update/delete remain chat-only
via the agent tools in `app/scheduled_tasks/agent_tools.py`.

Auth: every endpoint requires a verified Clerk JWT (via `require_app_user`),
which resolves the caller to a single AppUser in a single workspace. The
repository layer enforces permission rules per scope; we surface those as
HTTP status codes (404 / 403 / 400) instead of leaking domain exceptions.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth.clerk import ResolvedAppUser, require_app_user
from app.scheduled_tasks import repository as repo

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/scheduled-tasks", tags=["scheduled-tasks"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #


class ScheduledTaskOut(BaseModel):
    """Serialized scheduled task for the web. Mirrors the DB row plus the
    derived `cron_human` (left null here; the frontend formats it with
    cronstrue to avoid duplicating cron-locale code in two languages)."""

    id: str
    name: str
    prompt: str
    cron_spec: str
    cron_human: str | None = None
    timezone: str
    scope: str
    destination_type: str
    destination_slack_id: str | None
    is_paused: bool
    paused_until: datetime | None
    last_run_at: datetime | None
    last_run_status: str | None
    last_run_error: str | None
    last_run_summary: str | None
    next_run_at: datetime | None
    created_at: datetime
    created_by_user_id: str | None


class TaskListResponse(BaseModel):
    tasks: list[ScheduledTaskOut]
    total_count: int


class PauseRequest(BaseModel):
    until: str | None = Field(
        default=None,
        description="YYYY-MM-DD (UTC). Null or absent = paused indefinitely.",
    )


def _serialize(task) -> ScheduledTaskOut:  # type: ignore[no-untyped-def]
    """Repo objects -> response model. Centralized so all three endpoints
    return the same shape."""
    return ScheduledTaskOut(
        id=str(task.id),
        name=task.name,
        prompt=task.prompt,
        cron_spec=task.cron_spec,
        cron_human=None,  # frontend formats via cronstrue
        timezone=task.timezone,
        scope=task.scope,
        destination_type=task.destination_type,
        destination_slack_id=task.destination_slack_id,
        is_paused=task.is_paused,
        paused_until=task.paused_until,
        last_run_at=task.last_run_at,
        last_run_status=task.last_run_status,
        last_run_error=task.last_run_error,
        last_run_summary=task.last_run_summary,
        next_run_at=task.next_run_at,
        created_at=task.created_at,
        created_by_user_id=str(task.created_by_user_id) if task.created_by_user_id else None,
    )


# --------------------------------------------------------------------------- #
# GET /api/scheduled-tasks
# --------------------------------------------------------------------------- #


@router.get("", response_model=TaskListResponse)
async def list_scheduled_tasks(
    filter: Literal["mine", "system", "all"] = "mine",
    user: ResolvedAppUser = Depends(require_app_user),
) -> TaskListResponse:
    tasks = await repo.list_tasks(
        user.workspace_id, user.app_user_id, filter_mode=filter
    )
    return TaskListResponse(
        tasks=[_serialize(t) for t in tasks],
        total_count=len(tasks),
    )


# --------------------------------------------------------------------------- #
# POST /api/scheduled-tasks/{id_or_name}/pause
# --------------------------------------------------------------------------- #


def _parse_until_iso(raw: str | None) -> datetime | None:
    """Parse a YYYY-MM-DD string to a UTC-midnight datetime. Returns None
    for null/empty input. Raises HTTPException(400) on a malformed value
    -- the web app should validate before sending, but we defend at the
    boundary too."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        d = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"`until` must be YYYY-MM-DD; got {raw!r}",
        ) from exc
    return datetime.combine(d, time(0, 0, 0, tzinfo=timezone.utc))


def _domain_error_to_http(exc: repo.ScheduledTaskError) -> HTTPException:
    """Map domain exceptions to HTTP status codes. Keeps the API handlers
    free of repeated try/except chains and ensures all three endpoints
    surface errors consistently."""
    if isinstance(exc, repo.TaskNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, repo.TaskPermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, repo.TaskValidationError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, repo.TaskNameConflict):
        return HTTPException(status_code=409, detail=str(exc))
    # Unknown subclass: 500 with generic message; the str(exc) is logged but
    # not leaked.
    log.warning("scheduled_task_api_unknown_domain_error", error=str(exc))
    return HTTPException(status_code=500, detail="internal error")


@router.post("/{id_or_name}/pause", response_model=ScheduledTaskOut)
async def pause_scheduled_task(
    id_or_name: str,
    body: PauseRequest | None = None,
    user: ResolvedAppUser = Depends(require_app_user),
) -> ScheduledTaskOut:
    until = _parse_until_iso(body.until if body else None)
    try:
        task = await repo.pause_task(
            user.workspace_id, user.app_user_id, id_or_name, until=until
        )
    except repo.ScheduledTaskError as exc:
        raise _domain_error_to_http(exc) from exc
    return _serialize(task)


# --------------------------------------------------------------------------- #
# POST /api/scheduled-tasks/{id_or_name}/resume
# --------------------------------------------------------------------------- #


@router.post("/{id_or_name}/resume", response_model=ScheduledTaskOut)
async def resume_scheduled_task(
    id_or_name: str,
    user: ResolvedAppUser = Depends(require_app_user),
) -> ScheduledTaskOut:
    try:
        task = await repo.resume_task(
            user.workspace_id, user.app_user_id, id_or_name
        )
    except repo.ScheduledTaskError as exc:
        raise _domain_error_to_http(exc) from exc
    return _serialize(task)


__all__ = ["router"]
