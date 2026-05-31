"""Per-workspace installation store backing Bolt's OAuth flow.

Bolt's `AsyncInstallationStore` is the seam where it persists tokens after a
user completes the install OAuth dance, and where it retrieves them for the
`authorize` callback. We back it with our existing `workspace` table:
- `bot_token` (Fernet-encrypted) stays the canonical token slot.
- `bot_user_id` + `bot_scopes` + `installed_at` track install metadata.

We don't support enterprise installs in this slice (single-team installs
only); the methods accept enterprise_id args from Bolt but ignore them.
If you ever need Enterprise Grid, extend with an `enterprise` table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import structlog
from slack_sdk.oauth.installation_store.async_installation_store import (
    AsyncInstallationStore,
)
from slack_sdk.oauth.installation_store.models.bot import Bot
from slack_sdk.oauth.installation_store.models.installation import Installation
from sqlalchemy import select

from app.auth.clerk_provisioning import provision_for_installer
from app.db.models import Workspace
from app.db.session import get_session
from app.scheduled_tasks.repository import seed_system_tasks_for_workspace
from app.slack.crypto import TokenCryptoError, decrypt_token, encrypt_token
from app.slack.tokens import invalidate_token_cache

log = structlog.get_logger(__name__)


class MisterrInstallationStore(AsyncInstallationStore):
    """Maps Bolt's Installation model to our `workspace` row.

    On `async_save`: upsert the row by team_id with Fernet-encrypted bot_token,
    bot_user_id, scopes, installed_at. Invalidates the token cache so the next
    event picks up the new credentials immediately.

    On `async_find_installation` / `async_find_bot`: lookup by team_id, decrypt
    the token, reconstruct a Bot/Installation. Returns None for unknown teams
    so Bolt cleanly drops events from workspaces we never installed in.
    """

    async def async_save(self, installation: Installation) -> None:
        team_id = installation.team_id
        if not team_id:
            log.warning("install_save_missing_team_id")
            return
        plain = installation.bot_token or ""
        if not plain:
            log.warning("install_save_missing_bot_token", team_id=team_id)
            return
        enc = encrypt_token(plain)
        scopes = installation.bot_scopes or []
        scopes_str = ",".join(scopes) if isinstance(scopes, list) else str(scopes)
        async with get_session() as session:
            ws = (
                await session.execute(
                    select(Workspace).where(Workspace.slack_team_id == team_id)
                )
            ).scalar_one_or_none()
            if ws is None:
                ws = Workspace(slack_team_id=team_id, name=installation.team_name)
                session.add(ws)
                await session.flush()
            ws.bot_token = enc
            ws.bot_user_id = installation.bot_user_id
            ws.bot_scopes = scopes_str
            if installation.team_name and not ws.name:
                ws.name = installation.team_name
            ws.installed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await session.commit()
            ws_id = ws.id
            ws_home_channel = ws.bot_home_channel_id
        invalidate_token_cache()
        log.info(
            "workspace_installed",
            team_id=team_id,
            team_name=installation.team_name,
            bot_user_id=installation.bot_user_id,
            scopes_count=len(scopes) if isinstance(scopes, list) else None,
        )
        # Seed Misterr's system scheduled tasks (workflow-discovery + daily-brief)
        # for this workspace. Idempotent via INSERT ... ON CONFLICT so re-installs
        # don't duplicate rows. bot_home_channel_id may still be NULL here -- the
        # rows are seeded anyway and the scheduler treats NULL destination as a
        # logged failure until admin configures the channel.
        try:
            seeded = await seed_system_tasks_for_workspace(ws_id, ws_home_channel)
            log.info(
                "workspace_install_seed_complete",
                workspace_id=str(ws_id),
                team_id=team_id,
                seeded=seeded,
            )
        except Exception as exc:  # noqa: BLE001
            # Don't fail the install because seeding failed; the startup hook
            # in main.py retries idempotently.
            log.warning(
                "workspace_install_seed_failed",
                workspace_id=str(ws_id),
                team_id=team_id,
                error=str(exc),
            )
        # Provision the Clerk Organization for this workspace (slice T-5).
        # Best-effort: if the installer doesn't have a Clerk user yet
        # (Slack-first onboarding), provision_for_installer returns None and
        # logs a deferred event. The web app's first-login provision endpoint
        # picks it up later. Never fail the install over Clerk hiccups.
        installer_uid = getattr(installation, "user_id", None)
        if installer_uid:
            try:
                org_id = await provision_for_installer(ws_id, installer_uid)
                if org_id:
                    log.info(
                        "workspace_install_clerk_org_ready",
                        workspace_id=str(ws_id),
                        team_id=team_id,
                        clerk_org_id=org_id,
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "workspace_install_clerk_org_failed",
                    workspace_id=str(ws_id),
                    team_id=team_id,
                    error=str(exc),
                )

    async def async_save_bot(self, bot: Bot) -> None:
        # Bolt sometimes saves just the bot (e.g. after a token rotation). The
        # `async_save` path covers the full install; this one is narrower.
        if not bot.team_id or not bot.bot_token:
            return
        enc = encrypt_token(bot.bot_token)
        async with get_session() as session:
            ws = (
                await session.execute(
                    select(Workspace).where(Workspace.slack_team_id == bot.team_id)
                )
            ).scalar_one_or_none()
            if ws is None:
                return
            ws.bot_token = enc
            if bot.bot_user_id:
                ws.bot_user_id = bot.bot_user_id
            await session.commit()
        invalidate_token_cache()

    async def async_find_installation(
        self,
        *,
        enterprise_id: Optional[str],
        team_id: Optional[str],
        user_id: Optional[str] = None,
        is_enterprise_install: Optional[bool] = False,
    ) -> Optional[Installation]:
        if not team_id:
            return None
        async with get_session() as session:
            ws = (
                await session.execute(
                    select(Workspace).where(Workspace.slack_team_id == team_id)
                )
            ).scalar_one_or_none()
        if ws is None or not ws.bot_token:
            return None
        try:
            token = decrypt_token(ws.bot_token)
        except TokenCryptoError as exc:
            log.warning("install_find_decrypt_failed", team_id=team_id, error=str(exc))
            return None
        return Installation(
            app_id=None,
            enterprise_id=enterprise_id,
            team_id=team_id,
            team_name=ws.name,
            bot_token=token,
            bot_id=None,
            bot_user_id=ws.bot_user_id,
            bot_scopes=ws.bot_scopes.split(",") if ws.bot_scopes else [],
            user_id=user_id,
        )

    async def async_find_bot(
        self,
        *,
        enterprise_id: Optional[str],
        team_id: Optional[str],
        is_enterprise_install: Optional[bool] = False,
    ) -> Optional[Bot]:
        installation = await self.async_find_installation(
            enterprise_id=enterprise_id, team_id=team_id
        )
        if installation is None:
            return None
        return Bot(
            app_id=installation.app_id,
            enterprise_id=installation.enterprise_id,
            team_id=installation.team_id,
            bot_token=installation.bot_token,
            bot_id=installation.bot_id,
            bot_user_id=installation.bot_user_id,
            bot_scopes=installation.bot_scopes,
            installed_at=0,
        )

    async def async_delete_installation(
        self,
        *,
        enterprise_id: Optional[str],
        team_id: Optional[str],
        user_id: Optional[str] = None,
    ) -> None:
        if not team_id:
            return
        async with get_session() as session:
            ws = (
                await session.execute(
                    select(Workspace).where(Workspace.slack_team_id == team_id)
                )
            ).scalar_one_or_none()
            if ws is None:
                return
            # Match CLI uninstall: clear credentials, preserve per-tenant data.
            ws.bot_token = None
            ws.bot_user_id = None
            ws.bot_scopes = None
            await session.commit()
        invalidate_token_cache()
        log.info("workspace_uninstalled", team_id=team_id)

    async def async_delete_bot(self, **kwargs) -> None:
        await self.async_delete_installation(**kwargs)
