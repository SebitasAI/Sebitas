"""Read-only /admin endpoints (slice T-8).

Gated on PLATFORM_ADMINS env var (see app/auth/clerk.py). All endpoints
are CROSS-workspace: an admin sees data across every tenant. No mutations
in v1 -- impersonation, deletions, edits live in a separate slice once
we've designed the audit trail.

Why a separate router instead of admin-only handlers under /api/...:
keeps the URL surface obvious + lets us strip /admin in a CORS or
rate-limit layer later without false positives.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.auth.clerk import (
    ClerkClaims,
    is_platform_admin_email,
    require_clerk_user,
    require_platform_admin,
)
from app.db.models import (
    AppUser,
    IntegrationConnection,
    ScheduledTask,
    Skill,
    SlackUser,
    Workspace,
)
from app.db.session import get_session

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


# Internal AppUser rows the platform creates on demand to drive its own
# flows (scheduled tasks fire as "SYSTEM_SCHEDULED", future system actors
# would land here too). Hidden from /admin views + excluded from
# user_count so the admin sees only real humans/bots from Slack.
#
# Why not a column or an `is_system` flag: only one row per workspace and
# the pattern is well-contained. If we ever ship a third system actor,
# promote this to a regex or a proper flag.
_HIDDEN_SLACK_USER_IDS: tuple[str, ...] = ("SYSTEM_SCHEDULED",)


# --------------------------------------------------------------------------- #
# Identity probe
# --------------------------------------------------------------------------- #


class AdminMeResponse(BaseModel):
    is_admin: bool
    email: str | None


@router.get("/me")
async def admin_me(
    clerk: ClerkClaims = Depends(require_clerk_user),
) -> AdminMeResponse:
    """Cheap probe the web can call to decide whether to show the /admin
    link in the sidebar. Returns 200 either way -- no 403 -- so the nav
    doesn't render based on error codes."""
    return AdminMeResponse(
        is_admin=is_platform_admin_email(clerk.email),
        email=clerk.email,
    )


# --------------------------------------------------------------------------- #
# Workspaces
# --------------------------------------------------------------------------- #


class WorkspaceSummary(BaseModel):
    id: str
    slack_team_id: str
    name: str | None
    installed_at: datetime | None
    bot_user_id: str | None
    bot_home_channel_id: str | None
    user_count: int
    skill_count: int
    scheduled_task_count: int
    integration_count: int


class WorkspacesResponse(BaseModel):
    workspaces: list[WorkspaceSummary]
    total_count: int


@router.get("/workspaces", response_model=WorkspacesResponse)
async def list_workspaces(
    _: ClerkClaims = Depends(require_platform_admin),
) -> WorkspacesResponse:
    """Workspaces + a few counts for the overview tab. One query per
    aggregate to keep the SQL legible; if the workspace count crosses a
    couple hundred, switch to a single CTE."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Workspace).order_by(Workspace.created_at.desc())
            )
        ).scalars().all()
        if not rows:
            return WorkspacesResponse(workspaces=[], total_count=0)
        ws_ids = [w.id for w in rows]

        user_counts = dict(
            (
                await session.execute(
                    select(AppUser.workspace_id, func.count())
                    .where(
                        AppUser.workspace_id.in_(ws_ids),
                        AppUser.slack_user_id.notin_(_HIDDEN_SLACK_USER_IDS),
                    )
                    .group_by(AppUser.workspace_id)
                )
            ).all()
        )
        skill_counts = dict(
            (
                await session.execute(
                    select(Skill.workspace_id, func.count())
                    .where(Skill.workspace_id.in_(ws_ids))
                    .group_by(Skill.workspace_id)
                )
            ).all()
        )
        task_counts = dict(
            (
                await session.execute(
                    select(ScheduledTask.workspace_id, func.count())
                    .where(ScheduledTask.workspace_id.in_(ws_ids))
                    .group_by(ScheduledTask.workspace_id)
                )
            ).all()
        )
        integration_counts = dict(
            (
                await session.execute(
                    select(IntegrationConnection.workspace_id, func.count())
                    .where(IntegrationConnection.workspace_id.in_(ws_ids))
                    .group_by(IntegrationConnection.workspace_id)
                )
            ).all()
        )

    summaries = [
        WorkspaceSummary(
            id=str(w.id),
            slack_team_id=w.slack_team_id,
            name=w.name,
            installed_at=w.installed_at,
            bot_user_id=w.bot_user_id,
            bot_home_channel_id=w.bot_home_channel_id,
            user_count=int(user_counts.get(w.id, 0)),
            skill_count=int(skill_counts.get(w.id, 0)),
            scheduled_task_count=int(task_counts.get(w.id, 0)),
            integration_count=int(integration_counts.get(w.id, 0)),
        )
        for w in rows
    ]
    return WorkspacesResponse(workspaces=summaries, total_count=len(summaries))


# --------------------------------------------------------------------------- #
# Users in a given workspace
# --------------------------------------------------------------------------- #


class UserSummary(BaseModel):
    app_user_id: str
    slack_user_id: str
    display_name: str | None
    real_name: str | None
    email: str | None
    tz: str | None
    is_bot: bool
    deleted: bool


class WorkspaceUsersResponse(BaseModel):
    workspace_id: str
    workspace_name: str | None
    users: list[UserSummary]
    total_count: int


@router.get("/workspaces/{workspace_id}/users", response_model=WorkspaceUsersResponse)
async def list_workspace_users(
    workspace_id: str,
    _: ClerkClaims = Depends(require_platform_admin),
) -> WorkspaceUsersResponse:
    import uuid as _uuid

    try:
        ws_uuid = _uuid.UUID(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid workspace_id") from exc

    async with get_session() as session:
        ws = (
            await session.execute(
                select(Workspace).where(Workspace.id == ws_uuid)
            )
        ).scalar_one_or_none()
        if ws is None:
            raise HTTPException(status_code=404, detail="workspace not found")

        # LEFT JOIN AppUser <-> SlackUser by (workspace_id, slack_user_id).
        # AppUser is the source of truth for who has interacted with Misterr
        # (one row per Slack user the agent ever served). SlackUser is the
        # cached roster (everyone in the workspace per users.list, including
        # people who never DM'd the bot). We surface AppUsers and decorate
        # with SlackUser fields when available.
        rows = (
            await session.execute(
                select(AppUser, SlackUser)
                .outerjoin(
                    SlackUser,
                    (SlackUser.workspace_id == AppUser.workspace_id)
                    & (SlackUser.slack_user_id == AppUser.slack_user_id),
                )
                .where(
                    AppUser.workspace_id == ws_uuid,
                    AppUser.slack_user_id.notin_(_HIDDEN_SLACK_USER_IDS),
                )
                .order_by(AppUser.created_at.desc())
            )
        ).all()

    users = [
        UserSummary(
            app_user_id=str(au.id),
            slack_user_id=au.slack_user_id,
            display_name=su.display_name if su else None,
            real_name=su.real_name if su else None,
            email=su.email if su else None,
            tz=su.tz if su else None,
            is_bot=su.is_bot if su else False,
            deleted=su.deleted if su else False,
        )
        for au, su in rows
    ]
    return WorkspaceUsersResponse(
        workspace_id=str(ws.id),
        workspace_name=ws.name,
        users=users,
        total_count=len(users),
    )


# --------------------------------------------------------------------------- #
# Scheduled tasks across workspaces
# --------------------------------------------------------------------------- #


class AdminScheduledTaskRow(BaseModel):
    id: str
    workspace_id: str
    workspace_name: str | None
    name: str
    scope: str
    cron_spec: str
    timezone: str
    is_paused: bool
    fire_once: bool
    prompt_is_literal: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_run_status: str | None
    created_at: datetime


class AdminScheduledTasksResponse(BaseModel):
    tasks: list[AdminScheduledTaskRow]
    total_count: int


@router.get("/scheduled-tasks", response_model=AdminScheduledTasksResponse)
async def list_all_scheduled_tasks(
    workspace_id: str | None = None,
    _: ClerkClaims = Depends(require_platform_admin),
) -> AdminScheduledTasksResponse:
    """Every scheduled task in the platform, optionally filtered to one
    workspace via the `workspace_id` query param. Newest first."""
    async with get_session() as session:
        stmt = select(ScheduledTask, Workspace.name).join(
            Workspace, Workspace.id == ScheduledTask.workspace_id
        )
        if workspace_id:
            import uuid as _uuid
            try:
                stmt = stmt.where(ScheduledTask.workspace_id == _uuid.UUID(workspace_id))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid workspace_id") from exc
        stmt = stmt.order_by(ScheduledTask.created_at.desc()).limit(500)
        rows = (await session.execute(stmt)).all()

    tasks = [
        AdminScheduledTaskRow(
            id=str(t.id),
            workspace_id=str(t.workspace_id),
            workspace_name=ws_name,
            name=t.name,
            scope=t.scope,
            cron_spec=t.cron_spec,
            timezone=t.timezone,
            is_paused=t.is_paused,
            fire_once=t.fire_once,
            prompt_is_literal=t.prompt_is_literal,
            next_run_at=t.next_run_at,
            last_run_at=t.last_run_at,
            last_run_status=t.last_run_status,
            created_at=t.created_at,
        )
        for t, ws_name in rows
    ]
    return AdminScheduledTasksResponse(tasks=tasks, total_count=len(tasks))


# --------------------------------------------------------------------------- #
# Skills across workspaces
# --------------------------------------------------------------------------- #


class AdminSkillRow(BaseModel):
    id: str
    workspace_id: str
    workspace_name: str | None
    name: str
    description: str
    scope: str
    activation_default: str
    source: str
    version: int
    size_bytes: int
    created_by_user_id: str | None
    created_at: datetime


class AdminSkillsResponse(BaseModel):
    skills: list[AdminSkillRow]
    total_count: int


@router.get("/skills", response_model=AdminSkillsResponse)
async def list_all_skills(
    workspace_id: str | None = None,
    _: ClerkClaims = Depends(require_platform_admin),
) -> AdminSkillsResponse:
    async with get_session() as session:
        stmt = select(Skill, Workspace.name).join(
            Workspace, Workspace.id == Skill.workspace_id
        )
        if workspace_id:
            import uuid as _uuid
            try:
                stmt = stmt.where(Skill.workspace_id == _uuid.UUID(workspace_id))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid workspace_id") from exc
        stmt = stmt.order_by(Skill.created_at.desc()).limit(500)
        rows = (await session.execute(stmt)).all()

    skills = [
        AdminSkillRow(
            id=str(s.id),
            workspace_id=str(s.workspace_id),
            workspace_name=ws_name,
            name=s.name,
            description=s.description,
            scope=s.scope,
            activation_default=s.activation_default,
            source=s.source,
            version=s.version,
            size_bytes=s.size_bytes,
            created_by_user_id=str(s.created_by_user_id) if s.created_by_user_id else None,
            created_at=s.created_at,
        )
        for s, ws_name in rows
    ]
    return AdminSkillsResponse(skills=skills, total_count=len(skills))


# --------------------------------------------------------------------------- #
# Integrations across workspaces
# --------------------------------------------------------------------------- #


class AdminIntegrationRow(BaseModel):
    id: str
    workspace_id: str
    workspace_name: str | None
    app: str
    provider: str
    status: str
    created_at: datetime


class AdminIntegrationsResponse(BaseModel):
    integrations: list[AdminIntegrationRow]
    total_count: int


@router.get("/integrations", response_model=AdminIntegrationsResponse)
async def list_all_integrations(
    workspace_id: str | None = None,
    _: ClerkClaims = Depends(require_platform_admin),
) -> AdminIntegrationsResponse:
    async with get_session() as session:
        stmt = select(IntegrationConnection, Workspace.name).join(
            Workspace, Workspace.id == IntegrationConnection.workspace_id
        )
        if workspace_id:
            import uuid as _uuid
            try:
                stmt = stmt.where(IntegrationConnection.workspace_id == _uuid.UUID(workspace_id))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid workspace_id") from exc
        stmt = stmt.order_by(IntegrationConnection.created_at.desc()).limit(500)
        rows = (await session.execute(stmt)).all()

    integrations = [
        AdminIntegrationRow(
            id=str(r.id),
            workspace_id=str(r.workspace_id),
            workspace_name=ws_name,
            app=r.app,
            provider=r.provider,
            status=r.status,
            created_at=r.created_at,
        )
        for r, ws_name in rows
    ]
    return AdminIntegrationsResponse(integrations=integrations, total_count=len(integrations))


__all__ = ["router"]
