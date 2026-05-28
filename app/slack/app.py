"""Bolt AsyncApp + Socket Mode handler with multi-workspace authorize.

The `authorize` callback resolves the bot token per-event by `team_id`,
reading from the Workspace row (Fernet-decrypted). This is what makes the
SAME app serve N workspaces from one process.

Socket Mode is still the transport (the app-level `xapp-` is one per app, not
per workspace). When we move to public distribution / App Directory, switch
to HTTP events + Bolt's OAuth handlers; the `authorize` plumbing here stays
the same.
"""

from __future__ import annotations

import structlog
from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
from slack_bolt.app.async_app import AsyncApp
from slack_sdk.oauth.installation_store.models.bot import Bot

from app.config import get_settings
from app.slack.handlers import register_handlers
from app.slack.tokens import get_bot_token_by_team

log = structlog.get_logger(__name__)


async def _authorize(enterprise_id, team_id, **_kwargs):
    """Resolve the bot token for the workspace this event is from. Returns
    None if the workspace isn't installed -- Bolt will then short-circuit the
    event (event is ignored, no handler runs). That's the right behaviour:
    we should never act for a workspace we don't have credentials for."""
    if not team_id:
        return None
    pair = await get_bot_token_by_team(team_id)
    if pair is None:
        log.warning("authorize_missing_workspace", team_id=team_id)
        return None
    bot_token, bot_user_id = pair
    # Bolt expects an AuthorizeResult-like object; the modern Bolt async path
    # accepts a Bot model directly.
    return Bot(
        app_id=None,
        enterprise_id=enterprise_id,
        team_id=team_id,
        bot_token=bot_token,
        bot_id=None,
        bot_user_id=bot_user_id,
        installed_at=0,
    )


def build_app() -> AsyncApp:
    # `signing_secret` is not strictly required for Socket Mode (events are
    # validated by the xapp- WebSocket auth), but Bolt asks for SOMETHING at
    # init when no static token is given. We pass a sentinel that's never
    # actually used; HTTP Events mode (later slice) will need a real one.
    return _build_with_authorize()


def _build_with_authorize() -> AsyncApp:
    app = AsyncApp(
        signing_secret="not-used-in-socket-mode",
        authorize=_authorize,
    )
    register_handlers(app)
    return app


def build_socket_handler(app: AsyncApp) -> AsyncSocketModeHandler:
    settings = get_settings()
    return AsyncSocketModeHandler(app, settings.slack_app_token)
