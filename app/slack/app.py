"""Bolt AsyncApp + Socket Mode handler construction."""

from __future__ import annotations

from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
from slack_bolt.app.async_app import AsyncApp

from app.config import get_settings
from app.slack.handlers import register_handlers


def build_app() -> AsyncApp:
    settings = get_settings()
    app = AsyncApp(token=settings.slack_bot_token)
    register_handlers(app)
    return app


def build_socket_handler(app: AsyncApp) -> AsyncSocketModeHandler:
    settings = get_settings()
    return AsyncSocketModeHandler(app, settings.slack_app_token)
