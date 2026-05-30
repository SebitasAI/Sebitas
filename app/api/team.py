"""Team management REST endpoints (slice T-5).

Wraps Clerk's Organization Members API behind the same Bearer-JWT-auth
flow the rest of the web app uses. The "team" is the Clerk Organization
that maps 1:1 to the calling user's active Slack workspace.

Permissions:
- Listing members: any org member.
- Inviting / removing / syncing Slack: only org:admin (role check on the
  Clerk claims surfaced by `require_app_user`).
- Provisioning: any signed-in Clerk user; the endpoint walks SlackUser
  cache to find unprovisioned workspaces where the calling email is in
  the roster.

The frontend reads members via Clerk's own React hooks
(`useOrganization`, etc.) for the simple read path; these endpoints
exist for the actions that need server-side privileged ops (sending
invites with custom redirect, syncing Slack roster, provisioning) and
for clients (mobile, future API consumers) without Clerk's React SDK.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.auth import clerk_backend as clerk_api
from app.auth.clerk import ResolvedAppUser, require_app_user, require_clerk_user, ClerkClaims
from app.auth.clerk_provisioning import (
    provision_and_backfill_all_workspaces,
    provision_legacy_workspace,
)
from app.db.models import AppUser, SlackUser, Workspace
from app.db.session import get_session

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/team", tags=["team"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #


class TeamMember(BaseModel):
    """One member of the current Clerk organization, denormalized for the
    UI's Team page."""

    clerk_user_id: str
    role: str  # "org:admin" or "org:member"
    email: str | None = None
    name: str | None = None
    image_url: str | None = None
    # Linked AppUser id (if the member has interacted with Misterr in
    # Slack). Null for web-only members.
    app_user_id: str | None = None
    slack_user_id: str | None = None
    joined_at: str | None = None


class TeamMembersResponse(BaseModel):
    members: list[TeamMember]
    total: int


class InviteRequest(BaseModel):
    # Plain str + cheap regex shape check. Clerk's create-invitation
    # endpoint validates the email properly and 422s on bad shape, so we
    # don't need pydantic[email] / email-validator just for this surface.
    email: str = Field(min_length=3, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: str = Field(default="org:member", pattern=r"^org:(admin|member)$")
    redirect_url: str | None = None


class InviteResponse(BaseModel):
    invitation_id: str
    email: str
    role: str


class SyncSlackRequest(BaseModel):
    """Action the frontend wants the sync to take:
      - "preview": return the diff without writing.
      - "apply": remove org members Slack marks deleted/deactivated.
    """

    mode: str = Field(default="preview", pattern=r"^(preview|apply)$")


class SyncSlackDiffEntry(BaseModel):
    clerk_user_id: str
    email: str | None
    reason: str  # "slack_deleted", "slack_not_in_workspace", etc.


class SyncSlackResponse(BaseModel):
    mode: str
    to_remove: list[SyncSlackDiffEntry]
    removed: list[str] = Field(default_factory=list)  # clerk_user_ids removed in apply mode


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _require_admin(user: ResolvedAppUser, clerk_role: str | None) -> None:
    """Block non-admin members from privileged ops. Reads the org role
    that `verify_clerk_jwt` parsed from the JWT (not from a separate
    Clerk lookup -- the role lives in the signed token)."""
    if clerk_role != "org:admin":
        raise HTTPException(
            status_code=403,
            detail="Only workspace admins can perform this action.",
        )


async def _current_workspace_and_org(user: ResolvedAppUser) -> tuple[Workspace, str]:
    """Return the calling user's Workspace + its clerk_org_id, or raise
    404/409 if the org isn't provisioned yet."""
    async with get_session() as session:
        ws = (
            await session.execute(
                select(Workspace).where(Workspace.id == user.workspace_id)
            )
        ).scalar_one()
    if not ws.clerk_org_id:
        raise HTTPException(
            status_code=409,
            detail=(
                "This workspace is not yet linked to a Clerk organization. "
                "Run provisioning (POST /api/team/provision)."
            ),
        )
    return ws, ws.clerk_org_id


# --------------------------------------------------------------------------- #
# GET /api/team/members
# --------------------------------------------------------------------------- #


@router.get("/members", response_model=TeamMembersResponse)
async def list_team_members(
    user: ResolvedAppUser = Depends(require_app_user),
) -> TeamMembersResponse:
    _, org_id = await _current_workspace_and_org(user)
    try:
        raw = await clerk_api.list_org_members(org_id, limit=200)
    except clerk_api.ClerkApiError as exc:
        log.warning("team_members_clerk_failed", org_id=org_id, error=str(exc))
        raise HTTPException(status_code=502, detail="Could not load org members") from exc

    # Enrich each member with their AppUser link (if any) so the UI can
    # show Slack identity alongside Clerk identity.
    clerk_user_ids = [
        (m.get("public_user_data") or {}).get("user_id")
        for m in raw
        if (m.get("public_user_data") or {}).get("user_id")
    ]
    app_user_by_clerk: dict[str, AppUser] = {}
    if clerk_user_ids:
        async with get_session() as session:
            rows = (
                await session.execute(
                    select(AppUser).where(
                        AppUser.workspace_id == user.workspace_id,
                        AppUser.clerk_user_id.in_(clerk_user_ids),
                    )
                )
            ).scalars().all()
            app_user_by_clerk = {a.clerk_user_id: a for a in rows if a.clerk_user_id}

    members: list[TeamMember] = []
    for m in raw:
        pud = m.get("public_user_data") or {}
        cuid = pud.get("user_id")
        if not cuid:
            continue
        au = app_user_by_clerk.get(cuid)
        members.append(
            TeamMember(
                clerk_user_id=cuid,
                role=m.get("role") or "org:member",
                email=pud.get("identifier") or pud.get("email_address"),
                name=" ".join(
                    p for p in (pud.get("first_name"), pud.get("last_name")) if p
                ).strip() or None,
                image_url=pud.get("image_url"),
                app_user_id=str(au.id) if au else None,
                slack_user_id=au.slack_user_id if au else None,
                joined_at=m.get("created_at"),
            )
        )
    return TeamMembersResponse(members=members, total=len(members))


# --------------------------------------------------------------------------- #
# POST /api/team/invite (admin only)
# --------------------------------------------------------------------------- #


@router.post("/invite", response_model=InviteResponse)
async def invite_team_member(
    body: InviteRequest,
    user: ResolvedAppUser = Depends(require_app_user),
    clerk: ClerkClaims = Depends(require_clerk_user),
) -> InviteResponse:
    _require_admin(user, clerk.org_role)
    _, org_id = await _current_workspace_and_org(user)
    try:
        inv = await clerk_api.create_org_invitation(
            org_id,
            email_address=str(body.email),
            inviter_user_id=clerk.sub,
            role=body.role,
            redirect_url=body.redirect_url,
        )
    except clerk_api.ClerkApiError as exc:
        # Clerk returns 422 on duplicate / 400 on bad email shape -- bubble
        # the detail so the frontend can show it.
        raise HTTPException(status_code=exc.status if 400 <= exc.status < 500 else 502, detail=exc.body) from exc
    log.info(
        "team_invite_sent",
        org_id=org_id,
        invited_email=str(body.email),
        inviter=clerk.sub,
        role=body.role,
    )
    return InviteResponse(
        invitation_id=inv.get("id", ""),
        email=str(body.email),
        role=body.role,
    )


# --------------------------------------------------------------------------- #
# DELETE /api/team/members/{clerk_user_id} (admin only)
# --------------------------------------------------------------------------- #


@router.delete("/members/{target_clerk_user_id}", status_code=204)
async def remove_team_member(
    target_clerk_user_id: str,
    user: ResolvedAppUser = Depends(require_app_user),
    clerk: ClerkClaims = Depends(require_clerk_user),
) -> None:
    _require_admin(user, clerk.org_role)
    if target_clerk_user_id == clerk.sub:
        raise HTTPException(
            status_code=400, detail="Use Clerk's account settings to leave the org."
        )
    _, org_id = await _current_workspace_and_org(user)
    try:
        await clerk_api.remove_org_member(org_id, target_clerk_user_id)
    except clerk_api.ClerkApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc.body)) from exc

    # Also clear the local AppUser link so the row doesn't claim membership
    # of an org the user is no longer part of. We do NOT delete the AppUser
    # itself -- Slack data (scheduled tasks, skills) stays for audit.
    async with get_session() as session:
        row = (
            await session.execute(
                select(AppUser).where(
                    AppUser.workspace_id == user.workspace_id,
                    AppUser.clerk_user_id == target_clerk_user_id,
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            row.clerk_user_id = None
            await session.commit()

    log.info(
        "team_member_removed",
        org_id=org_id,
        target_clerk_user_id=target_clerk_user_id,
        actor=clerk.sub,
    )


# --------------------------------------------------------------------------- #
# POST /api/team/sync-slack (admin only)
# --------------------------------------------------------------------------- #


@router.post("/sync-slack", response_model=SyncSlackResponse)
async def sync_slack_members(
    body: SyncSlackRequest,
    user: ResolvedAppUser = Depends(require_app_user),
    clerk: ClerkClaims = Depends(require_clerk_user),
) -> SyncSlackResponse:
    """Compare current Clerk org members against Slack's cached roster.
    Removes org members whose linked SlackUser is `deleted=true` (Slack
    marked them deactivated or deleted).

    For the diff we only consider members WITH a linked AppUser. Web-only
    members (no slack_user_id) aren't candidates for "Slack says they're
    gone" since they were never in Slack to begin with.
    """
    _require_admin(user, clerk.org_role)
    _, org_id = await _current_workspace_and_org(user)

    try:
        members = await clerk_api.list_org_members(org_id, limit=500)
    except clerk_api.ClerkApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc.body)) from exc

    clerk_ids = [
        (m.get("public_user_data") or {}).get("user_id") for m in members
    ]
    clerk_ids = [c for c in clerk_ids if c]

    # Build local lookups: clerk_user_id -> AppUser, and SlackUser deletion state.
    async with get_session() as session:
        app_users = (
            await session.execute(
                select(AppUser).where(
                    AppUser.workspace_id == user.workspace_id,
                    AppUser.clerk_user_id.in_(clerk_ids),
                )
            )
        ).scalars().all()
        au_by_clerk = {a.clerk_user_id: a for a in app_users if a.clerk_user_id}
        if app_users:
            slack_users = (
                await session.execute(
                    select(SlackUser).where(
                        SlackUser.workspace_id == user.workspace_id,
                        SlackUser.slack_user_id.in_(
                            [a.slack_user_id for a in app_users]
                        ),
                    )
                )
            ).scalars().all()
            su_by_slack = {s.slack_user_id: s for s in slack_users}
        else:
            su_by_slack = {}

    to_remove: list[SyncSlackDiffEntry] = []
    for m in members:
        pud = m.get("public_user_data") or {}
        cuid = pud.get("user_id")
        if not cuid:
            continue
        if cuid == clerk.sub:
            # Never auto-remove the actor.
            continue
        au = au_by_clerk.get(cuid)
        if au is None:
            continue  # web-only member, no Slack record to check
        su = su_by_slack.get(au.slack_user_id)
        if su is None:
            to_remove.append(
                SyncSlackDiffEntry(
                    clerk_user_id=cuid,
                    email=pud.get("identifier"),
                    reason="slack_not_in_workspace",
                )
            )
            continue
        if su.deleted:
            to_remove.append(
                SyncSlackDiffEntry(
                    clerk_user_id=cuid,
                    email=su.email,
                    reason="slack_deleted",
                )
            )

    removed: list[str] = []
    if body.mode == "apply":
        for entry in to_remove:
            try:
                await clerk_api.remove_org_member(org_id, entry.clerk_user_id)
                removed.append(entry.clerk_user_id)
            except clerk_api.ClerkApiError as exc:
                log.warning(
                    "team_sync_remove_failed",
                    org_id=org_id,
                    clerk_user_id=entry.clerk_user_id,
                    error=str(exc),
                )
        # Local unlink, mirroring DELETE /members behaviour.
        if removed:
            async with get_session() as session:
                rows = (
                    await session.execute(
                        select(AppUser).where(
                            AppUser.workspace_id == user.workspace_id,
                            AppUser.clerk_user_id.in_(removed),
                        )
                    )
                ).scalars().all()
                for r in rows:
                    r.clerk_user_id = None
                await session.commit()
        log.info(
            "team_sync_applied",
            org_id=org_id,
            count=len(removed),
            actor=clerk.sub,
        )
    else:
        log.info(
            "team_sync_preview",
            org_id=org_id,
            candidates=len(to_remove),
        )

    return SyncSlackResponse(
        mode=body.mode,
        to_remove=to_remove,
        removed=removed,
    )


# --------------------------------------------------------------------------- #
# POST /api/team/provision (any authenticated user)
# --------------------------------------------------------------------------- #


class ProvisionResponse(BaseModel):
    orgs_created: int
    members_linked: int


@router.post("/provision", response_model=ProvisionResponse)
async def provision_my_workspaces(
    clerk: ClerkClaims = Depends(require_clerk_user),
) -> ProvisionResponse:
    """First-login trigger: look up the calling user's email, find every
    workspace where they appear in the SlackUser cache, and provision any
    that haven't been provisioned yet.

    Open to any signed-in Clerk user (no `require_app_user` because the
    point is to bootstrap an AppUser <-> org link). Idempotent: re-running
    is a no-op once everything is provisioned.
    """
    if not clerk.email:
        raise HTTPException(
            status_code=403, detail="Clerk session has no email; cannot provision."
        )

    needle = clerk.email.strip().lower()
    async with get_session() as session:
        ws_rows = (
            await session.execute(
                select(Workspace)
                .join(SlackUser, SlackUser.workspace_id == Workspace.id)
                .where(
                    Workspace.installed_at.is_not(None),
                    func.lower(SlackUser.email) == needle,
                    SlackUser.deleted == False,  # noqa: E712
                )
                .distinct()
            )
        ).scalars().all()

    orgs_created = 0
    for ws in ws_rows:
        if ws.clerk_org_id:
            continue
        new_id = await provision_legacy_workspace(ws.id)
        if new_id:
            orgs_created += 1

    # Always run the global backfill for the member-linking side effects;
    # this is bounded by AppUser count and idempotent.
    counts = await provision_and_backfill_all_workspaces()
    return ProvisionResponse(
        orgs_created=max(orgs_created, counts.get("orgs_created", 0)),
        members_linked=counts.get("members_linked", 0),
    )


__all__ = ["router"]
