"""REST endpoints for the Misterr web app's Automations page.

Peer to `app/api/scheduled_tasks.py`. The web only reads + pauses/resumes;
create/update/delete remain chat-only via the agent tools in
`app/automations/agent_tools.py`. This matches the Scheduled Tasks UX:
mutation flows through the agent (where the user gets a preview +
confirmation), while the web is a status console plus quick toggle.

Auth: every endpoint requires a verified Clerk JWT via `require_app_user`,
which resolves the caller to a single AppUser in a single workspace.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.clerk import ResolvedAppUser, require_app_user
from app.automations import repository as repo

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/automations", tags=["automations"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #


class AutomationOut(BaseModel):
    id: str
    name: str
    description: str | None
    trigger_type: str
    trigger_filter: dict[str, Any]
    action_type: str
    action_config: dict[str, Any]
    scope: str
    is_paused: bool
    last_fired_at: datetime | None
    last_fire_status: str | None
    last_fire_error: str | None
    fire_count: int
    created_at: datetime
    created_by_user_id: str | None
    owner_user_id: str | None


class AutomationRunOut(BaseModel):
    id: str
    automation_id: str | None
    automation_name_snapshot: str
    trigger_event: dict[str, Any]
    action_type: str
    action_config_snapshot: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None
    status: str
    output: str | None
    error: str | None


class AutomationListResponse(BaseModel):
    automations: list[AutomationOut]
    total_count: int


class AutomationRunsResponse(BaseModel):
    runs: list[AutomationRunOut]
    total_count: int


def _serialize(a) -> AutomationOut:  # type: ignore[no-untyped-def]
    return AutomationOut(
        id=str(a.id),
        name=a.name,
        description=a.description,
        trigger_type=a.trigger_type,
        trigger_filter=a.trigger_filter or {},
        action_type=a.action_type,
        action_config=a.action_config or {},
        scope=a.scope,
        is_paused=a.is_paused,
        last_fired_at=a.last_fired_at,
        last_fire_status=a.last_fire_status,
        last_fire_error=a.last_fire_error,
        fire_count=a.fire_count or 0,
        created_at=a.created_at,
        created_by_user_id=str(a.created_by_user_id) if a.created_by_user_id else None,
        owner_user_id=str(a.owner_user_id) if a.owner_user_id else None,
    )


def _serialize_run(r) -> AutomationRunOut:  # type: ignore[no-untyped-def]
    return AutomationRunOut(
        id=str(r.id),
        automation_id=str(r.automation_id) if r.automation_id else None,
        automation_name_snapshot=r.automation_name_snapshot,
        trigger_event=r.trigger_event or {},
        action_type=r.action_type,
        action_config_snapshot=r.action_config_snapshot or {},
        started_at=r.started_at,
        finished_at=r.finished_at,
        status=r.status,
        output=r.output,
        error=r.error,
    )


def _domain_error_to_http(exc: repo.AutomationError) -> HTTPException:
    if isinstance(exc, repo.AutomationNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, repo.AutomationPermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, repo.AutomationValidationError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, repo.AutomationNameConflict):
        return HTTPException(status_code=409, detail=str(exc))
    log.warning("automation_api_unknown_domain_error", error=str(exc))
    return HTTPException(status_code=500, detail="internal error")


# --------------------------------------------------------------------------- #
# GET /api/automations
# --------------------------------------------------------------------------- #


@router.get("", response_model=AutomationListResponse)
async def list_automations(
    filter: Literal["mine", "all"] = "mine",
    user: ResolvedAppUser = Depends(require_app_user),
) -> AutomationListResponse:
    automations = await repo.list_automations(
        workspace_id=user.workspace_id,
        current_user_id=user.app_user_id,
        only_mine=(filter == "mine"),
    )
    return AutomationListResponse(
        automations=[_serialize(a) for a in automations],
        total_count=len(automations),
    )


# --------------------------------------------------------------------------- #
# POST /api/automations/{handle}/pause
# --------------------------------------------------------------------------- #


@router.post("/{handle}/pause", response_model=AutomationOut)
async def pause_automation(
    handle: str,
    user: ResolvedAppUser = Depends(require_app_user),
) -> AutomationOut:
    try:
        a = await repo.pause_automation(
            workspace_id=user.workspace_id,
            current_user_id=user.app_user_id,
            handle=handle,
        )
    except repo.AutomationError as exc:
        raise _domain_error_to_http(exc) from exc
    return _serialize(a)


# --------------------------------------------------------------------------- #
# POST /api/automations/{handle}/resume
# --------------------------------------------------------------------------- #


@router.post("/{handle}/resume", response_model=AutomationOut)
async def resume_automation(
    handle: str,
    user: ResolvedAppUser = Depends(require_app_user),
) -> AutomationOut:
    try:
        a = await repo.resume_automation(
            workspace_id=user.workspace_id,
            current_user_id=user.app_user_id,
            handle=handle,
        )
    except repo.AutomationError as exc:
        raise _domain_error_to_http(exc) from exc
    return _serialize(a)


# --------------------------------------------------------------------------- #
# GET /api/automations/{handle}/runs
# --------------------------------------------------------------------------- #


@router.get("/{handle}/runs", response_model=AutomationRunsResponse)
async def list_automation_runs(
    handle: str,
    limit: int = 50,
    user: ResolvedAppUser = Depends(require_app_user),
) -> AutomationRunsResponse:
    """Newest first. Capped to `limit` rows; no pagination yet."""
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")
    # Resolve the handle first so the API behaves like scheduled-tasks
    # (404 for unknown automation rather than empty list).
    from app.db.session import get_session

    async with get_session() as session:
        try:
            automation = await repo.resolve_automation(
                session, user.workspace_id, handle
            )
        except repo.AutomationError as exc:
            raise _domain_error_to_http(exc) from exc

    runs = await repo.list_runs_for_automation(
        workspace_id=user.workspace_id,
        automation_id=automation.id,
        limit=limit,
    )
    return AutomationRunsResponse(
        runs=[_serialize_run(r) for r in runs],
        total_count=len(runs),
    )


__all__ = ["router"]
