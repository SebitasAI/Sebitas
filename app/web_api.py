"""Private endpoints for the Misterr web app (server-to-server only).

These routes are NOT meant to be called from the browser. The Misterr Next.js
app uses a Clerk-authenticated route handler that proxies through to here,
attaching `X-Misterr-Web-Token` with the shared secret from Doppler. The
backend verifies the token and serves the response.

Why not browser-direct: we don't want the browser to send the Clerk user's
email straight to the backend without server-side validation; routing
through the Next.js server lets us bind the lookup to the verified Clerk
session.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import SlackUser, Workspace
from app.db.session import get_session

log = structlog.get_logger(__name__)
router = APIRouter()


def _verify_token(request: Request) -> None:
    expected = (get_settings().misterr_web_api_key or "").strip()
    got = request.headers.get("x-misterr-web-token", "").strip()
    if not expected or not got or expected != got:
        log.warning("web_api_unauthorized", path=request.url.path)
        raise HTTPException(status_code=401, detail="unauthorized")


@router.get("/api/web/workspaces")
async def list_workspaces_for_user(
    request: Request,
    email: str | None = None,
    org_ids: str | None = None,
):
    """Return the workspaces where Misterr is installed AND the user is
    a member, matched by EITHER:

      - email against the cached Slack roster (works for users whose
        Clerk email = Slack email), OR
      - workspace.clerk_org_id against the user's Clerk Org IDs (works
        for users invited via Clerk -- their Clerk email may differ
        from their Slack email, e.g. invited members).

    The Next.js route handler is responsible for binding both inputs
    to the verified Clerk session. The backend trusts the shared
    secret + the fact that the request originated server-side.
    """
    _verify_token(request)
    needle = (email or "").strip().lower()
    org_id_list = [s.strip() for s in (org_ids or "").split(",") if s.strip()]
    if not needle and not org_id_list:
        return {"workspaces": []}

    async with get_session() as session:
        workspace_ids: set[uuid.UUID] = set()

        # Path 1: email match against the cached roster.
        if needle:
            slack_users = (
                await session.execute(
                    select(SlackUser).where(
                        func.lower(SlackUser.email) == needle,
                        SlackUser.deleted == False,  # noqa: E712
                    )
                )
            ).scalars().all()
            for su in slack_users:
                workspace_ids.add(su.workspace_id)

        # Path 2: Clerk Org membership.
        if org_id_list:
            org_ws = (
                await session.execute(
                    select(Workspace.id).where(
                        Workspace.clerk_org_id.in_(org_id_list),
                    )
                )
            ).scalars().all()
            workspace_ids.update(org_ws)

        if not workspace_ids:
            return {"workspaces": []}

        workspaces = (
            await session.execute(
                select(Workspace).where(
                    Workspace.id.in_(workspace_ids),
                    Workspace.installed_at.is_not(None),
                )
            )
        ).scalars().all()

    return {
        "workspaces": [
            {
                "id": str(w.id),
                "name": w.name or w.slack_team_id,
                "slackTeamId": w.slack_team_id,
                "iconUrl": w.slack_team_icon_url,
                "primaryEmail": needle or None,
                # `clerk_org_id` lets the install gate decide whether the
                # currently-active Clerk organization corresponds to a
                # Slack-installed workspace. Without it, the gate could
                # only count workspaces globally and would miss the
                # "user just created a fresh Clerk org" case.
                "clerkOrgId": w.clerk_org_id,
            }
            for w in workspaces
        ]
    }
