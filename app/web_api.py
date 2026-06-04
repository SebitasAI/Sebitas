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
async def list_workspaces_for_user(request: Request, email: str):
    """Return the workspaces where Misterr is installed AND the given email
    appears in the cached Slack roster. The email comes from the Clerk
    session on the web side; the backend trusts the shared-secret + the
    fact that the request originated from the Next.js server."""
    _verify_token(request)
    needle = (email or "").strip().lower()
    if not needle:
        return {"workspaces": []}

    async with get_session() as session:
        # Case-insensitive match: Slack-cached emails preserve the case
        # the user typed at signup, but Clerk normalises to lowercase.
        # Compare via LOWER() on both sides to avoid missed matches.
        slack_users = (
            await session.execute(
                select(SlackUser).where(
                    func.lower(SlackUser.email) == needle,
                    SlackUser.deleted == False,  # noqa: E712
                )
            )
        ).scalars().all()
        if not slack_users:
            return {"workspaces": []}
        workspace_ids: list[uuid.UUID] = list({su.workspace_id for su in slack_users})
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
                "primaryEmail": needle,
            }
            for w in workspaces
        ]
    }
