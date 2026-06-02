"""REST endpoints for the Misterr web app's Automations page.

Peer to `app/api/scheduled_tasks.py`. The web only reads + pauses/resumes
+ rotates the direct-URL secret. Create / update / delete remain
chat-only via the agent tools in `app/automations/agent_tools.py`
(where the agent shows a preview and the user confirms before any
upstream trigger provisioning happens).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.clerk import ResolvedAppUser, require_app_user
from app.automations import repository as repo
from app.automations import triggers as _triggers

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/automations", tags=["automations"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #


class AutomationOut(BaseModel):
    id: str
    name: str
    description: str | None
    source: str
    prompt_template: str
    destination_channel: str | None
    # Only populated for source=direct so we can show the URL in the UI.
    # NEVER returned for other sources (the URL is internal there).
    webhook_url: str | None
    external_trigger_id: str | None
    trigger_metadata: dict[str, Any]
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
    trigger_payload: dict[str, Any]
    prompt_template_snapshot: str
    rendered_prompt: str | None
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
    webhook_url = None
    if a.source == "direct" and a.webhook_secret:
        webhook_url = _triggers.direct_webhook_url(a.webhook_secret)
    return AutomationOut(
        id=str(a.id),
        name=a.name,
        description=a.description,
        source=a.source,
        prompt_template=a.prompt_template,
        destination_channel=a.destination_channel,
        webhook_url=webhook_url,
        external_trigger_id=a.external_trigger_id,
        trigger_metadata=a.trigger_metadata or {},
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
        trigger_payload=r.trigger_payload or {},
        prompt_template_snapshot=r.prompt_template_snapshot,
        rendered_prompt=r.rendered_prompt,
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
# POST /api/automations/{handle}/rotate-url
# --------------------------------------------------------------------------- #


@router.post("/{handle}/rotate-url", response_model=AutomationOut)
async def rotate_webhook_url(
    handle: str,
    user: ResolvedAppUser = Depends(require_app_user),
) -> AutomationOut:
    """Generate a new webhook_secret for source=direct. The old URL
    stops working immediately."""
    try:
        a = await repo.rotate_webhook_secret(
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
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")
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
