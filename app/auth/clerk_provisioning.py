"""Provision Clerk Organizations from Slack workspaces (slice T-5).

Two entry points:
- `provision_for_installer` is called from `install_store.async_save` right
  after a Slack OAuth install completes. It tries to create a Clerk org
  using the installer's email. If the installer doesn't have a Clerk user
  yet (they installed via Slack but never signed in to the web app), the
  function logs a "deferred" event and returns None -- a later call from
  the web side picks it up.
- `provision_legacy_workspace` powers the one-shot backfill for the 4
  workspaces that exist pre-migration. The "owner" is the oldest AppUser
  in the workspace (per Sam's call); we resolve their email from SlackUser
  and use that to find the Clerk user.

Both go through the same internal `_create_org_and_link` so the side
effects (Clerk org creation, `workspace.clerk_org_id` write, owner
membership, owner AppUser.clerk_user_id link) stay in one place.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import func, select

from app.auth import clerk_backend as clerk_api
from app.db.models import AppUser, SlackUser, Workspace
from app.db.repository import upsert_app_user
from app.db.session import get_session

log = structlog.get_logger(__name__)


async def _link_app_user_clerk_id(
    workspace_id: uuid.UUID, slack_user_id: str, clerk_user_id: str
) -> None:
    """Ensure an AppUser exists for (workspace, slack_user) and link it to
    `clerk_user_id`.

    This used to be a no-op when no AppUser row existed yet -- the rationale
    being "the Clerk user joined the org but hasn't DM'd Misterr from Slack".
    That was the root cause of freshly-installed workspaces showing "0 users"
    and the installer getting locked out of the web app (403 "DM the bot
    first") despite having completed the install/signup flow. We now create
    the membership row on the spot so signing up + completing the Slack flow
    is enough to be a connected user."""
    async with get_session() as session:
        await upsert_app_user(
            session, workspace_id, slack_user_id, clerk_user_id=clerk_user_id
        )
        await session.commit()


async def _create_org_and_link(
    workspace: Workspace, *, owner_clerk_user_id: str
) -> str:
    """Create the Clerk org for `workspace`, add `owner_clerk_user_id` as
    admin, and persist `workspace.clerk_org_id`. Returns the new org id.

    Idempotent for the "already provisioned" case: if the workspace already
    has a clerk_org_id we just add the user as a member (if they aren't
    already) and return the existing id."""
    if workspace.clerk_org_id:
        # Already provisioned. Just ensure this user is in the org.
        existing = await clerk_api.find_org_membership(
            workspace.clerk_org_id, owner_clerk_user_id
        )
        if existing is None:
            await clerk_api.add_org_member(
                workspace.clerk_org_id,
                user_id=owner_clerk_user_id,
                role="org:admin",
            )
        return workspace.clerk_org_id

    org_name = workspace.name or workspace.slack_team_id
    metadata = {
        "slack_team_id": workspace.slack_team_id,
        "misterr_workspace_id": str(workspace.id),
    }
    org = await clerk_api.create_organization(
        name=org_name,
        created_by=owner_clerk_user_id,
        public_metadata=metadata,
    )
    org_id = org["id"]
    log.info(
        "clerk_org_created",
        workspace_id=str(workspace.id),
        slack_team_id=workspace.slack_team_id,
        clerk_org_id=org_id,
        owner_clerk_user_id=owner_clerk_user_id,
    )

    # `create_organization` returns the created org with `created_by` as
    # admin already. No extra add_org_member call needed for the owner.

    async with get_session() as session:
        # Re-fetch to avoid stale-row writes if the caller passed a detached
        # ORM object.
        row = (
            await session.execute(
                select(Workspace).where(Workspace.id == workspace.id)
            )
        ).scalar_one()
        row.clerk_org_id = org_id
        await session.commit()

    return org_id


async def _resolve_installer_email(
    workspace_id: uuid.UUID,
    installer_slack_user_id: str,
    bot_token: str | None,
) -> str | None:
    """Get the installer's email so we can map them to a Clerk user.

    Tries the cached SlackUser roster first (zero-latency, works once
    the roster sync has happened). Falls back to calling Slack's
    `users.info` directly with the workspace's freshly-minted bot
    token -- this works at install time when the roster is still empty
    (the classic chicken-and-egg the install path used to lose to).

    Returns None when both paths fail (network error, scope missing,
    user has no email on file).
    """
    # Path 1: cached roster.
    async with get_session() as session:
        slack_user = (
            await session.execute(
                select(SlackUser).where(
                    SlackUser.workspace_id == workspace_id,
                    SlackUser.slack_user_id == installer_slack_user_id,
                )
            )
        ).scalar_one_or_none()
    if slack_user is not None and slack_user.email:
        return slack_user.email

    # Path 2: live Slack `users.info` with the bot token. The OAuth
    # response handed install_store.async_save the bot token already,
    # so this round-trip is cheap (one HTTP call) and unblocks
    # provisioning during the same install handler.
    if not bot_token:
        return None
    try:
        from slack_sdk.web.async_client import AsyncWebClient

        client = AsyncWebClient(token=bot_token)
        resp = await client.users_info(user=installer_slack_user_id)
        # `users:read.email` scope is required for the `email` field.
        # If the workspace didn't grant it the field is absent and we
        # return None -- the legacy "deferred to web-side provision"
        # path still covers that edge case.
        profile = (resp.data or {}).get("user", {}) if isinstance(resp.data, dict) else {}
        prof = profile.get("profile") or {}
        email = (prof.get("email") or "").strip()
        return email or None
    except Exception as exc:  # noqa: BLE001
        log.info(
            "clerk_provision_users_info_failed",
            workspace_id=str(workspace_id),
            installer_slack_user_id=installer_slack_user_id,
            error=str(exc)[:200],
        )
        return None


async def provision_for_installer(
    workspace_id: uuid.UUID, installer_slack_user_id: str
) -> str | None:
    """Provision the Clerk org for a freshly-installed Slack workspace.

    Resolves the installer's email via `_resolve_installer_email`,
    which tries the cached roster first then falls back to a live
    Slack `users.info` call against the workspace's bot token. The
    fallback removes the install-vs-roster-sync race that previously
    left fresh workspaces with `clerk_org_id=NULL` indefinitely.

    If the installer's email maps to a Clerk user, create the org.
    Otherwise log "deferred" and return None -- the web-side provision
    endpoint takes over once the installer signs up.
    """
    async with get_session() as session:
        ws = (
            await session.execute(select(Workspace).where(Workspace.id == workspace_id))
        ).scalar_one_or_none()
        if ws is None:
            log.warning(
                "clerk_provision_workspace_missing", workspace_id=str(workspace_id)
            )
            return None
        if ws.clerk_org_id:
            return ws.clerk_org_id  # already provisioned
        # Bot token captured for the live Slack lookup fallback.
        bot_token_enc = ws.bot_token

    bot_token: str | None = None
    if bot_token_enc:
        try:
            from app.slack.crypto import TokenCryptoError, decrypt_token

            bot_token = decrypt_token(bot_token_enc)
        except Exception as exc:  # noqa: BLE001
            log.info(
                "clerk_provision_token_decrypt_failed",
                workspace_id=str(workspace_id), error=str(exc)[:200],
            )
            bot_token = None

    email = await _resolve_installer_email(
        workspace_id, installer_slack_user_id, bot_token
    )
    if not email:
        log.info(
            "clerk_org_deferred_no_email",
            workspace_id=str(workspace_id),
            installer_slack_user_id=installer_slack_user_id,
            reason=(
                "installer not in SlackUser cache, users.info call failed "
                "or returned no email"
            ),
        )
        return None

    try:
        user = await clerk_api.find_user_by_email(email)
    except clerk_api.ClerkApiError as exc:
        log.warning(
            "clerk_org_install_lookup_failed",
            workspace_id=str(workspace_id),
            email=email,
            error=str(exc),
        )
        return None

    if user is None:
        log.info(
            "clerk_org_deferred_no_clerk_user",
            workspace_id=str(workspace_id),
            email=email,
        )
        return None

    org_id = await _create_org_and_link(ws, owner_clerk_user_id=user["id"])
    await _link_app_user_clerk_id(workspace_id, installer_slack_user_id, user["id"])
    return org_id


async def provision_legacy_workspace(workspace_id: uuid.UUID) -> str | None:
    """Backfill path: pick the workspace's OLDEST AppUser as the org owner.
    Per Sam's call we don't have a record of who installed each legacy
    workspace, so the oldest AppUser is a reasonable proxy (typically the
    workspace's earliest active user).

    Returns the clerk_org_id provisioned (or already present), None if no
    suitable owner could be resolved."""
    async with get_session() as session:
        ws = (
            await session.execute(select(Workspace).where(Workspace.id == workspace_id))
        ).scalar_one_or_none()
        if ws is None:
            return None
        if ws.clerk_org_id:
            # Already provisioned; ensure each AppUser-without-clerk_user_id
            # still gets linked. The caller can loop separately if needed.
            return ws.clerk_org_id

        # Oldest AppUser; tie-break by id stably for determinism.
        oldest = (
            await session.execute(
                select(AppUser)
                .where(AppUser.workspace_id == workspace_id)
                .order_by(AppUser.created_at.asc(), AppUser.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if oldest is None:
            log.info(
                "clerk_org_backfill_no_appuser",
                workspace_id=str(workspace_id),
                slack_team_id=ws.slack_team_id,
            )
            return None

        slack_user = (
            await session.execute(
                select(SlackUser).where(
                    SlackUser.workspace_id == workspace_id,
                    SlackUser.slack_user_id == oldest.slack_user_id,
                )
            )
        ).scalar_one_or_none()

    if slack_user is None or not slack_user.email:
        log.info(
            "clerk_org_backfill_no_email",
            workspace_id=str(workspace_id),
            owner_app_user_id=str(oldest.id),
        )
        return None

    try:
        user = await clerk_api.find_user_by_email(slack_user.email)
    except clerk_api.ClerkApiError as exc:
        log.warning(
            "clerk_org_backfill_lookup_failed",
            workspace_id=str(workspace_id),
            email=slack_user.email,
            error=str(exc),
        )
        return None

    if user is None:
        log.info(
            "clerk_org_backfill_no_clerk_user",
            workspace_id=str(workspace_id),
            email=slack_user.email,
        )
        return None

    org_id = await _create_org_and_link(ws, owner_clerk_user_id=user["id"])
    await _link_app_user_clerk_id(workspace_id, oldest.slack_user_id, user["id"])
    return org_id


async def backfill_workspace_member(
    *, workspace_id: uuid.UUID, app_user_id: uuid.UUID
) -> bool:
    """For an existing AppUser in a workspace whose Clerk org is already
    provisioned: look up their email via SlackUser, find their Clerk user,
    add them as `org:member` of the workspace's Clerk org, and link
    `AppUser.clerk_user_id`.

    Idempotent: if the user is already an org member we still link the
    clerk_user_id locally. Returns True if a link was created (either DB
    or Clerk side), False if we couldn't (no Clerk user, no email, etc.).
    """
    async with get_session() as session:
        app_user = (
            await session.execute(select(AppUser).where(AppUser.id == app_user_id))
        ).scalar_one_or_none()
        if app_user is None or app_user.workspace_id != workspace_id:
            return False
        ws = (
            await session.execute(select(Workspace).where(Workspace.id == workspace_id))
        ).scalar_one_or_none()
        if ws is None or not ws.clerk_org_id:
            return False
        slack_user = (
            await session.execute(
                select(SlackUser).where(
                    SlackUser.workspace_id == workspace_id,
                    SlackUser.slack_user_id == app_user.slack_user_id,
                )
            )
        ).scalar_one_or_none()

    if slack_user is None or not slack_user.email:
        return False

    try:
        user = await clerk_api.find_user_by_email(slack_user.email)
    except clerk_api.ClerkApiError as exc:
        log.warning(
            "clerk_org_backfill_member_lookup_failed",
            workspace_id=str(workspace_id),
            app_user_id=str(app_user_id),
            error=str(exc),
        )
        return False
    if user is None:
        return False

    clerk_user_id = user["id"]

    # Ensure org membership exists.
    try:
        existing = await clerk_api.find_org_membership(
            ws.clerk_org_id, clerk_user_id
        )
        if existing is None:
            await clerk_api.add_org_member(
                ws.clerk_org_id, user_id=clerk_user_id, role="org:member"
            )
    except clerk_api.ClerkApiError as exc:
        log.warning(
            "clerk_org_backfill_add_member_failed",
            workspace_id=str(workspace_id),
            app_user_id=str(app_user_id),
            clerk_user_id=clerk_user_id,
            error=str(exc),
        )
        return False

    await _link_app_user_clerk_id(workspace_id, app_user.slack_user_id, clerk_user_id)
    return True


async def provision_and_backfill_all_workspaces() -> dict[str, int]:
    """Idempotent one-shot pass over all installed workspaces. Provisions
    orgs for any workspace without clerk_org_id, then links every AppUser
    in those workspaces to its Clerk user (and adds them as org members).

    Returns counts for visibility:
      - orgs_created: workspaces that had clerk_org_id provisioned
      - members_linked: AppUsers whose clerk_user_id was just populated
    """
    orgs_created = 0
    members_linked = 0

    async with get_session() as session:
        workspaces = (
            await session.execute(
                select(Workspace).where(Workspace.installed_at.is_not(None))
            )
        ).scalars().all()

    for ws in workspaces:
        if not ws.clerk_org_id:
            org_id = await provision_legacy_workspace(ws.id)
            if org_id:
                orgs_created += 1
            else:
                # Could not provision -> skip member linking too.
                continue

        async with get_session() as session:
            app_users = (
                await session.execute(
                    select(AppUser).where(
                        AppUser.workspace_id == ws.id,
                        AppUser.clerk_user_id.is_(None),
                    )
                )
            ).scalars().all()

        for au in app_users:
            ok = await backfill_workspace_member(
                workspace_id=ws.id, app_user_id=au.id
            )
            if ok:
                members_linked += 1

    log.info(
        "clerk_org_backfill_summary",
        orgs_created=orgs_created,
        members_linked=members_linked,
    )
    return {"orgs_created": orgs_created, "members_linked": members_linked}


__all__ = [
    "provision_for_installer",
    "provision_legacy_workspace",
    "backfill_workspace_member",
    "provision_and_backfill_all_workspaces",
]
