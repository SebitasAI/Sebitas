"""Action dispatchers for automations.

Two action types in v1:

- `slack_notify`: post a Block-Kit-free message in Slack. Config:
    {
      "channel": "C0123" | "D0123" | null,   # optional; null -> DM creator
      "text": "Trace {trace_id} broke: {error}",  # SafeDict template
    }

- `agent_run`: kick off an agent run with a templated prompt. Config:
    {
      "channel": "C0123" | null,   # optional; null -> DM creator
      "prompt": "Open thread about {entity} and {action}",
    }

Both share the SafeDict template engine -- `str.format_map(SafeDict(...))`
fills in keys present in the event payload and leaves the rest as
`{unknown}` placeholders rather than raising KeyError. That's the right
default for v1: a misconfigured automation produces a slightly ugly
message instead of a fired-but-failed run, and the unfilled key is a
visible breadcrumb."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import select

from app.automations.events import Event
from app.db.models import AppUser, Automation, Workspace
from app.db.session import get_session
from app.slack.crypto import TokenCryptoError, decrypt_token

log = structlog.get_logger(__name__)


SYSTEM_ACTOR_SLACK_USER_ID = "SYSTEM_AUTOMATION"


class ActionSkipped(Exception):
    """Raised when an action declines to run for a benign reason (e.g.
    missing creator for DM default). The router records this as
    `skipped` rather than `failed`."""


class SafeDict(dict):
    """`str.format_map` engine that leaves unknown keys as literal
    `{key}` instead of raising KeyError. Stringifies all values."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render(template: str, event: Event) -> str:
    """Render a v1 template. Variables come from the event payload's
    `data` dict plus a few standard fields (`type`, `occurred_at`)."""
    ctx: dict[str, Any] = dict(event.data)
    ctx["type"] = event.type
    ctx["occurred_at"] = event.occurred_at.isoformat()
    try:
        return template.format_map(SafeDict({k: str(v) for k, v in ctx.items()}))
    except Exception as exc:
        # Bad escape, mismatched braces -- log and fall back to raw.
        log.warning("automation_template_render_failed", error=str(exc))
        return template


async def _workspace_bot_client(workspace_id: uuid.UUID) -> tuple[AsyncWebClient, Workspace]:
    """Build an AsyncWebClient with this workspace's decrypted bot token.
    Raises if the workspace isn't installed or the token is unreadable."""
    async with get_session() as session:
        ws = (
            await session.execute(select(Workspace).where(Workspace.id == workspace_id))
        ).scalar_one_or_none()
    if ws is None:
        raise RuntimeError(f"workspace {workspace_id} not found")
    if not ws.bot_token:
        raise RuntimeError(f"workspace {workspace_id} has no bot_token (not installed)")
    try:
        bot_token = decrypt_token(ws.bot_token)
    except TokenCryptoError as exc:
        raise RuntimeError(f"could not decrypt bot_token: {exc}") from exc
    return AsyncWebClient(token=bot_token), ws


async def _resolve_default_dm_channel(
    client: AsyncWebClient, automation: Automation
) -> str:
    """When the automation didn't specify a channel, default to a DM with
    the creator. If there's no creator (system-scope automation) we
    raise ActionSkipped -- system automations MUST set an explicit channel."""
    if automation.created_by_user_id is None:
        raise ActionSkipped(
            "no channel configured and no creator to DM (system-scope?)"
        )
    async with get_session() as session:
        user = (
            await session.execute(
                select(AppUser).where(AppUser.id == automation.created_by_user_id)
            )
        ).scalar_one_or_none()
    if user is None or not user.slack_user_id:
        raise ActionSkipped("creator user has no slack_user_id; cannot DM")
    resp = await client.conversations_open(users=user.slack_user_id)
    channel_id = (
        resp.get("channel", {}).get("id") if isinstance(resp, dict) else resp["channel"]["id"]
    )
    if not channel_id:
        raise RuntimeError("conversations.open returned no channel id")
    return channel_id


async def _do_slack_notify(automation: Automation, event: Event) -> str:
    config = automation.action_config or {}
    template = config.get("text") or ""
    if not template:
        raise ActionSkipped("slack_notify: empty text template")

    client, _ws = await _workspace_bot_client(automation.workspace_id)
    channel = config.get("channel") or await _resolve_default_dm_channel(client, automation)
    rendered = _render(template, event)
    resp = await client.chat_postMessage(channel=channel, text=rendered)
    ts = resp.get("ts") if isinstance(resp, dict) else resp["ts"]
    return f"posted to {channel} ts={ts}"


async def _do_agent_run(automation: Automation, event: Event) -> str:
    """Kick off an agent run with the rendered prompt. Mirrors the
    scheduled-task scheduler's pattern: open a parent message in the
    destination, then call `run_agent` in that thread.

    `event.fire_depth + 1` is propagated via the contextvar so any
    events the agent emits are accounted for by the loop guard. The
    plumbing for that contextvar lives in events.py / runner.py."""
    config = automation.action_config or {}
    template = config.get("prompt") or ""
    if not template:
        raise ActionSkipped("agent_run: empty prompt template")

    client, ws = await _workspace_bot_client(automation.workspace_id)
    channel = config.get("channel") or await _resolve_default_dm_channel(client, automation)
    prompt = _render(template, event)

    parent_text = f":zap: Automation `{automation.name}`"
    post_resp = await client.chat_postMessage(channel=channel, text=parent_text)
    parent_ts = (
        post_resp.get("ts") if isinstance(post_resp, dict) else post_resp["ts"]
    )
    if not parent_ts:
        raise RuntimeError("could not post parent message")

    # Local import: runner imports automations downstream (it publishes
    # events). Defer to avoid the cycle at module load.
    from app.agent.runner import run_agent  # noqa: PLC0415
    from app.automations import events as _events  # noqa: PLC0415

    # Push the inherited fire_depth onto a contextvar so any new events
    # emitted by this run carry it. See events.py for the consumer side.
    token = _events.set_fire_depth(event.fire_depth + 1)
    try:
        await run_agent(
            client=client,
            team_id=ws.slack_team_id,
            slack_user_id=SYSTEM_ACTOR_SLACK_USER_ID,
            channel=channel,
            user_text=prompt,
            user_ts=parent_ts,
            conversation_key=parent_ts,
            reply_thread_ts=parent_ts,
            require_existing_thread=False,
            files=None,
            lock_handle=None,
        )
    finally:
        _events.reset_fire_depth(token)

    return f"agent_run dispatched: channel={channel} ts={parent_ts}"


_DISPATCH = {
    "slack_notify": _do_slack_notify,
    "agent_run": _do_agent_run,
}


async def dispatch(*, automation: Automation, event: Event) -> str | None:
    """Run the action matching `automation.action_type`. Returns a short
    one-line summary for the AutomationRun.output column. Raises
    ActionSkipped for benign no-ops; other exceptions become `failed`
    runs in the router."""
    handler = _DISPATCH.get(automation.action_type)
    if handler is None:
        raise RuntimeError(f"unknown action_type: {automation.action_type}")
    return await handler(automation, event)


__all__ = ["dispatch", "ActionSkipped", "SafeDict", "SYSTEM_ACTOR_SLACK_USER_ID"]
