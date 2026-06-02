"""Run the agent on an automation fire.

Single action in v1: render `prompt_template` against the webhook
payload (SafeDict so unknown keys stay as `{literal}`) and call
`run_agent` in the destination channel.

The destination is `automation.destination_channel` if set, otherwise
a DM with the automation's creator (resolved at fire time via
`conversations.open`). Mirrors the scheduled-task scheduler's pattern
of posting a parent message, then invoking `run_agent` in its thread."""

from __future__ import annotations

import json
import string
import uuid
from typing import Any

import structlog
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import select

from app.db.models import AppUser, Automation, Workspace
from app.db.session import get_session
from app.slack.crypto import TokenCryptoError, decrypt_token

log = structlog.get_logger(__name__)


# Marker actor for agent runs spawned by automations. Matches the
# scheduled-task convention (SYSTEM_SCHEDULED) so Langfuse traces +
# logs distinguish automation-triggered runs from human ones.
SYSTEM_ACTOR_SLACK_USER_ID = "SYSTEM_AUTOMATION"


class ActionSkipped(Exception):
    """Raised when an action declines to run for a benign reason (e.g.
    creator has no Slack id for the default DM). The router records
    this as `skipped` rather than `failed`."""


class SafeDict(dict):
    """`str.format_map` engine that leaves unknown keys as literal
    `{key}` instead of raising KeyError. Stringifies all values."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class _SafeFormatter(string.Formatter):
    """Treats the whole field name as a single dotted key into the
    SafeDict, rather than Python's default of "look up first segment,
    then attribute-access the rest". This lets `{data.trace_id}` work
    against a flat dict that holds `data.trace_id` as a literal key.

    On any failure (missing key, bad format spec, conversion error)
    we keep the original `{...}` in the output."""

    def get_field(self, field_name: str, args, kwargs):
        # Bypass Python's a.b.c attribute walk; the whole field name
        # is the key into the mapping.
        if isinstance(kwargs, SafeDict):
            return kwargs[field_name], field_name
        return super().get_field(field_name, args, kwargs)

    def format_field(self, value, format_spec):
        try:
            return super().format_field(value, format_spec)
        except Exception:
            # Bad format spec on a real value -> emit the value as-is.
            return str(value)


_FORMATTER = _SafeFormatter()


def _flatten(prefix: str, value: Any, out: dict[str, str]) -> None:
    """Walk nested dicts and emit dotted keys so a template can refer
    to `{data.error}` against a payload `{"data": {"error": "..."}}`.
    Non-dict/list leaves are stringified. Lists are emitted both as
    a JSON-encoded value (`{tags}` -> `["a","b"]`) AND by index
    (`{tags.0}` -> `"a"`), so users can pick the form they want."""
    if isinstance(value, dict):
        for k, v in value.items():
            child = f"{prefix}.{k}" if prefix else str(k)
            _flatten(child, v, out)
        # Also expose the dict at its prefix as JSON, in case the
        # user wants to dump the whole thing.
        if prefix:
            out[prefix] = json.dumps(value, default=str)
    elif isinstance(value, list):
        if prefix:
            out[prefix] = json.dumps(value, default=str)
        for i, item in enumerate(value):
            _flatten(f"{prefix}.{i}", item, out)
    else:
        if prefix:
            out[prefix] = "" if value is None else str(value)


def render_template(template: str, payload: dict[str, Any]) -> str:
    """Fill `{key}` and `{nested.key}` placeholders against `payload`.
    Unknown keys stay literal `{key}`. Never raises on payload contents
    -- the worst case is a slightly off-looking message, never a
    failed agent run."""
    flat: dict[str, str] = {}
    _flatten("", payload, flat)
    # Also expose the entire payload at the bareword `payload`.
    flat["payload"] = json.dumps(payload, default=str)
    try:
        return _FORMATTER.vformat(template, (), SafeDict(flat))
    except Exception as exc:
        log.warning("automation_template_render_failed", error=str(exc))
        return template


async def _workspace_bot_client(
    workspace_id: uuid.UUID,
) -> tuple[AsyncWebClient, Workspace]:
    """Build an AsyncWebClient with this workspace's decrypted bot
    token. Raises if not installed or token unreadable."""
    async with get_session() as session:
        ws = (
            await session.execute(select(Workspace).where(Workspace.id == workspace_id))
        ).scalar_one_or_none()
    if ws is None:
        raise RuntimeError(f"workspace {workspace_id} not found")
    if not ws.bot_token:
        raise RuntimeError(f"workspace {workspace_id} has no bot_token")
    try:
        bot_token = decrypt_token(ws.bot_token)
    except TokenCryptoError as exc:
        raise RuntimeError(f"could not decrypt bot_token: {exc}") from exc
    return AsyncWebClient(token=bot_token), ws


async def _resolve_destination_channel(
    client: AsyncWebClient, automation: Automation
) -> str:
    """Return the Slack channel id to post into. If automation has
    `destination_channel` set, that's it. Otherwise we DM the
    creator. Raises ActionSkipped if there's no creator + no
    explicit channel (e.g. orphaned system-scope automation)."""
    if automation.destination_channel:
        return automation.destination_channel
    if automation.created_by_user_id is None:
        raise ActionSkipped(
            "no destination_channel y sin creator para DM."
        )
    async with get_session() as session:
        user = (
            await session.execute(
                select(AppUser).where(AppUser.id == automation.created_by_user_id)
            )
        ).scalar_one_or_none()
    if user is None or not user.slack_user_id:
        raise ActionSkipped("creator no tiene slack_user_id; no puedo DM.")
    resp = await client.conversations_open(users=user.slack_user_id)
    channel_id = (
        resp.get("channel", {}).get("id")
        if isinstance(resp, dict)
        else resp["channel"]["id"]
    )
    if not channel_id:
        raise RuntimeError("conversations.open returned no channel id")
    return channel_id


async def fire_agent_run(
    automation: Automation, payload: dict[str, Any]
) -> tuple[str, str]:
    """Render template -> post a parent message -> run the agent in
    that thread. Returns (rendered_prompt, short_summary) for the
    run-log row."""
    rendered = render_template(automation.prompt_template, payload)
    client, ws = await _workspace_bot_client(automation.workspace_id)
    channel = await _resolve_destination_channel(client, automation)

    parent_text = f":zap: Automation `{automation.name}`"
    post_resp = await client.chat_postMessage(channel=channel, text=parent_text)
    parent_ts = (
        post_resp.get("ts") if isinstance(post_resp, dict) else post_resp["ts"]
    )
    if not parent_ts:
        raise RuntimeError("could not post parent message")

    # Local import: runner imports the DB/models layer that imports back
    # here. Defer to break the cycle at module load.
    from app.agent.runner import run_agent  # noqa: PLC0415

    await run_agent(
        client=client,
        team_id=ws.slack_team_id,
        slack_user_id=SYSTEM_ACTOR_SLACK_USER_ID,
        channel=channel,
        user_text=rendered,
        user_ts=parent_ts,
        conversation_key=parent_ts,
        reply_thread_ts=parent_ts,
        require_existing_thread=False,
        files=None,
        lock_handle=None,
    )
    return rendered, f"agent_run dispatched: channel={channel} ts={parent_ts}"


__all__ = [
    "fire_agent_run",
    "render_template",
    "ActionSkipped",
    "SafeDict",
    "SYSTEM_ACTOR_SLACK_USER_ID",
]
