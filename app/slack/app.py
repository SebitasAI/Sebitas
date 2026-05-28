"""Bolt AsyncApp + Socket Mode + OAuth install flow.

Three pieces coexist in this one app:
1. **Multi-workspace authorize**: `_authorize` resolves the bot token per
   event by `team_id` from the workspace row.
2. **Socket Mode** for events: one `xapp-` app-level token receives events
   for all installed workspaces (no inbound HTTPS needed for events).
3. **OAuth install flow** (HTTP): users hit `/slack/install` to start, Slack
   redirects to `/slack/oauth_redirect` with a code, Bolt exchanges it and
   our InstallationStore persists the token.

OAuth endpoints are exposed via FastAPI (mounted from `app/main.py`); events
keep using Socket Mode. This split lets us self-service install before
fully migrating to HTTP events + App Directory.
"""

from __future__ import annotations

import structlog
from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
from slack_bolt.app.async_app import AsyncApp
from slack_bolt.oauth.async_oauth_settings import AsyncOAuthSettings
from slack_sdk.oauth.installation_store.models.bot import Bot

from app.config import get_settings
from app.slack.handlers import register_handlers
from app.slack.install_store import SebitasInstallationStore
from app.slack.tokens import get_bot_token_by_team

log = structlog.get_logger(__name__)


_install_store = SebitasInstallationStore()


async def _authorize(enterprise_id, team_id, **_kwargs):
    """Resolve the bot token for the workspace this event is from. None ->
    Bolt drops the event (no handler runs). That's the right behaviour for
    workspaces we don't have credentials for."""
    if not team_id:
        return None
    pair = await get_bot_token_by_team(team_id)
    if pair is None:
        log.warning("authorize_missing_workspace", team_id=team_id)
        return None
    bot_token, bot_user_id = pair
    return Bot(
        app_id=None,
        enterprise_id=enterprise_id,
        team_id=team_id,
        bot_token=bot_token,
        bot_id=None,
        bot_user_id=bot_user_id,
        installed_at=0,
    )


def _oauth_settings() -> AsyncOAuthSettings | None:
    """OAuth requires SLACK_CLIENT_ID + SLACK_CLIENT_SECRET + SIGNING_SECRET.
    If any is missing we return None and Bolt boots without OAuth (Socket
    Mode + manual CLI install still work)."""
    s = get_settings()
    if not (s.slack_client_id and s.slack_client_secret and s.slack_signing_secret):
        return None
    return AsyncOAuthSettings(
        client_id=s.slack_client_id,
        client_secret=s.slack_client_secret,
        scopes=[scope.strip() for scope in s.slack_bot_scopes.split(",") if scope.strip()],
        user_scopes=[],
        installation_store=_install_store,
        install_path="/slack/install",
        redirect_uri_path="/slack/oauth_redirect",
    )


def build_app() -> AsyncApp:
    s = get_settings()
    oauth = _oauth_settings()
    # `signing_secret` is required by Bolt at init when not in pure-token mode.
    # For Socket Mode it's unused for event auth (events come via xapp-).
    # For OAuth callbacks it WILL be checked.
    signing_secret = s.slack_signing_secret or "not-used-in-socket-mode"
    if oauth:
        app = AsyncApp(
            signing_secret=signing_secret,
            authorize=_authorize,
            oauth_settings=oauth,
            installation_store=_install_store,
        )
        log.info("slack_app_built", oauth=True)
    else:
        app = AsyncApp(signing_secret=signing_secret, authorize=_authorize)
        log.info("slack_app_built", oauth=False, reason="missing client_id/secret/signing_secret")
    register_handlers(app)
    return app


def build_socket_handler(app: AsyncApp) -> AsyncSocketModeHandler:
    return AsyncSocketModeHandler(app, get_settings().slack_app_token)


def get_slack_app() -> AsyncApp:
    """Re-export for the FastAPI adapter in main.py."""
    return _slack_app_singleton


# Singleton: same instance used by the socket handler and the FastAPI mount.
_slack_app_singleton: AsyncApp | None = None


def init_slack_app() -> AsyncApp:
    """Build the app once at startup; subsequent calls return the same instance."""
    global _slack_app_singleton
    if _slack_app_singleton is None:
        _slack_app_singleton = build_app()
    return _slack_app_singleton
