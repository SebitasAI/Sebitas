"""Native Slack tools.

Critical context: Misterr IS a Slack app. The bot OAuth grants it
`channels:history`, `groups:history`, `mpim:history`, `im:history`,
`channels:read`, `users:read`, etc. on install. We DO NOT need to
"connect Slack" through Composio or Pipedream to read channel
history, list channels, or look up users -- the bot token already
covers all of that natively.

Without these tools the agent only sees Composio/Pipedream's Slack
toolkit, which (a) requires a separate user-installed integration
on top of the bot, and (b) fails confusingly with "integration not
connected" because nobody connected something they didn't know they
needed.

Side-effect imports: this module is imported from `app/agent/tools.py`
so `register(...)` runs at app boot and these tools appear in the
agent's catalog.

Tools registered:
- read_slack_channel: pull recent messages from a channel/DM/group
- read_slack_thread: pull all replies in a thread by parent ts
- list_slack_channels: list channels the bot has access to
- find_slack_channel: resolve a channel name to a C/G/D id
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from slack_sdk.web.async_client import AsyncWebClient

from app.agent.context import workspace_id_var
from app.agent.tools import Tool, register
from app.slack.tokens import get_bot_token_by_workspace

log = structlog.get_logger(__name__)


async def _client() -> AsyncWebClient | None:
    """Build an AsyncWebClient with the current workspace's bot token.
    Returns None when there's no workspace context or token. The tool
    handlers fall back to a user-visible error string in that case."""
    ws_str = workspace_id_var.get()
    if not ws_str:
        return None
    try:
        workspace_id = uuid.UUID(ws_str) if isinstance(ws_str, str) else ws_str
    except (ValueError, TypeError):
        return None
    pair = await get_bot_token_by_workspace(workspace_id)
    if pair is None:
        return None
    return AsyncWebClient(token=pair[0])


def _format_messages(messages: list[dict], include_thread_ts: bool = False) -> str:
    """Render Slack message dicts as a compact, model-friendly text
    block. One line per message: timestamp, author, snippet. Skips
    join/leave subtype noise."""
    lines: list[str] = []
    for m in messages:
        if m.get("subtype") in {
            "channel_join",
            "channel_leave",
            "channel_topic",
            "channel_purpose",
            "channel_name",
        }:
            continue
        ts = m.get("ts") or ""
        try:
            dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            time_str = dt.strftime("%Y-%m-%d %H:%M UTC")
        except (TypeError, ValueError):
            time_str = ts
        # Prefer user (real human) over bot_id, fall back to a generic.
        author = m.get("user") or m.get("bot_id") or "?"
        text = (m.get("text") or "").replace("\n", " ").strip()
        # Reactions are useful signal for "highlights" prompts.
        reactions = m.get("reactions") or []
        rxn_summary = (
            " [" + ", ".join(f"{r['name']}x{r.get('count', 1)}" for r in reactions) + "]"
            if reactions
            else ""
        )
        thread_marker = ""
        if include_thread_ts and m.get("thread_ts") and m.get("thread_ts") != ts:
            thread_marker = f" (reply in {m['thread_ts']})"
        # Indicate that the message kicked off a thread, so the agent
        # knows to call read_slack_thread on it if it wants the replies.
        replies_count = m.get("reply_count")
        thread_lead = (
            f" (+ {replies_count} replies in thread, ts={ts})"
            if replies_count
            else ""
        )
        lines.append(
            f"[{time_str}] <@{author}>: {text}{rxn_summary}{thread_marker}{thread_lead}"
        )
    return "\n".join(lines) if lines else "(no messages in range)"


# --------------------------------------------------------------------------- #
# read_slack_channel
# --------------------------------------------------------------------------- #


async def _read_slack_channel(
    channel: str,
    hours_back: float = 24.0,
    limit: int = 100,
) -> str:
    """Pull recent messages from a Slack channel/DM/group via the bot
    token. `channel` accepts C/G/D ids -- not channel names. For names
    use `find_slack_channel` first."""
    client = await _client()
    if client is None:
        return (
            "Error: no pude armar el cliente Slack (sin contexto de "
            "workspace o sin bot_token). Reinstalá el bot si esto persiste."
        )
    if not channel or not channel.strip():
        return "Error: el parámetro `channel` es obligatorio."
    oldest = (
        datetime.now(timezone.utc) - timedelta(hours=max(hours_back, 0.0))
    ).timestamp()
    try:
        resp = await client.conversations_history(
            channel=channel.strip(),
            limit=min(max(limit, 1), 200),
            oldest=str(oldest),
        )
    except Exception as exc:  # noqa: BLE001
        # Common cases worth surfacing verbatim: not_in_channel,
        # missing_scope, channel_not_found. We trust the SlackApiError
        # message to be clear enough.
        return f"Slack API error: {exc}"
    msgs = list(resp.get("messages", []) or [])
    msgs.sort(key=lambda m: float(m.get("ts") or 0))
    body = _format_messages(msgs, include_thread_ts=True)
    header = (
        f"Canal {channel} -- {len(msgs)} mensajes en las últimas "
        f"{hours_back:g}h:\n"
    )
    return header + body


register(
    Tool(
        name="read_slack_channel",
        description=(
            "Read recent messages from a Slack channel, DM, or group. "
            "USE ESTO para CUALQUIER pedido tipo 'resumime el canal', "
            "'qué se habló', 'highlights del día', 'top mensajes', etc. "
            "Misterr es una Slack app con scope `channels:history` / "
            "`groups:history` / `im:history` -- NO necesitás conectar "
            "Slack como integración via Composio/Pipedream para esto. "
            "`channel` es el id (C.../G.../D...); para resolverlo por "
            "nombre usá `find_slack_channel`. `hours_back` default 24h."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "Slack channel/DM/group id (C.../G.../D...).",
                },
                "hours_back": {
                    "type": "number",
                    "description": "How many hours of history to fetch. Default 24.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max messages to return. Default 100, max 200.",
                },
            },
            "required": ["channel"],
        },
        handler=_read_slack_channel,
    )
)


# --------------------------------------------------------------------------- #
# read_slack_thread
# --------------------------------------------------------------------------- #


async def _read_slack_thread(channel: str, thread_ts: str) -> str:
    """Read all replies in a Slack thread by parent ts. Returns the
    parent + all replies, oldest first."""
    client = await _client()
    if client is None:
        return "Error: no pude armar el cliente Slack."
    if not channel or not thread_ts:
        return "Error: `channel` y `thread_ts` son obligatorios."
    try:
        resp = await client.conversations_replies(
            channel=channel.strip(), ts=thread_ts.strip(), limit=200
        )
    except Exception as exc:  # noqa: BLE001
        return f"Slack API error: {exc}"
    msgs = list(resp.get("messages", []) or [])
    return f"Thread {thread_ts} en {channel} -- {len(msgs)} mensajes:\n" + _format_messages(
        msgs
    )


register(
    Tool(
        name="read_slack_thread",
        description=(
            "Read all replies in a Slack thread, given the channel id "
            "and the parent message ts. Útil después de "
            "`read_slack_channel` cuando ves que un mensaje tiene "
            "replies (`+ N replies in thread`) y querés el detalle."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "Slack channel/DM/group id where the thread lives.",
                },
                "thread_ts": {
                    "type": "string",
                    "description": "Parent message ts (e.g. '1748739231.000400').",
                },
            },
            "required": ["channel", "thread_ts"],
        },
        handler=_read_slack_thread,
    )
)


# --------------------------------------------------------------------------- #
# list_slack_channels
# --------------------------------------------------------------------------- #


async def _list_slack_channels(
    types: str = "public_channel,private_channel",
    limit: int = 100,
) -> str:
    """List channels the bot has access to. Filterable by type."""
    client = await _client()
    if client is None:
        return "Error: no pude armar el cliente Slack."
    valid_types = {
        "public_channel",
        "private_channel",
        "mpim",
        "im",
    }
    requested = [t.strip() for t in types.split(",") if t.strip()]
    bad = [t for t in requested if t not in valid_types]
    if bad:
        return (
            f"Error: `types` inválidos: {bad}. Válidos: "
            f"{sorted(valid_types)}."
        )
    try:
        resp = await client.conversations_list(
            types=",".join(requested) if requested else None,
            limit=min(max(limit, 1), 500),
            exclude_archived=True,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Slack API error: {exc}"
    channels = list(resp.get("channels", []) or [])
    lines = [
        f"- {c.get('name', '?')} (id={c.get('id')}, "
        f"members={c.get('num_members', '?')}, "
        f"is_private={c.get('is_private', False)})"
        for c in channels
    ]
    return f"{len(channels)} canales accesibles:\n" + "\n".join(lines)


register(
    Tool(
        name="list_slack_channels",
        description=(
            "List Slack channels the bot has access to. Default trae "
            "public + private channels (no DMs ni mpims salvo que los "
            "pidas en `types`). Útil cuando el usuario menciona un "
            "canal por nombre y necesitás el id, o cuando hace falta "
            "discovery."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "types": {
                    "type": "string",
                    "description": (
                        "Comma-separated: 'public_channel', 'private_channel', "
                        "'mpim', 'im'. Default 'public_channel,private_channel'."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max channels to return. Default 100.",
                },
            },
            "required": [],
        },
        handler=_list_slack_channels,
    )
)


# --------------------------------------------------------------------------- #
# find_slack_channel (by name)
# --------------------------------------------------------------------------- #


async def _find_slack_channel(name: str) -> str:
    """Resolve a channel name (with or without #) to a channel id by
    listing accessible channels and matching on lower-case name. Returns
    the id + a short status line, or a not-found message."""
    client = await _client()
    if client is None:
        return "Error: no pude armar el cliente Slack."
    needle = (name or "").strip().lstrip("#").lower()
    if not needle:
        return "Error: `name` es obligatorio."
    cursor: str | None = None
    found: list[dict[str, Any]] = []
    # Paginate -- some workspaces have hundreds of channels.
    for _ in range(20):  # safety cap
        try:
            params: dict[str, Any] = {
                "types": "public_channel,private_channel",
                "limit": 500,
                "exclude_archived": True,
            }
            if cursor:
                params["cursor"] = cursor
            resp = await client.conversations_list(**params)
        except Exception as exc:  # noqa: BLE001
            return f"Slack API error: {exc}"
        for c in resp.get("channels", []) or []:
            if (c.get("name") or "").lower() == needle:
                found.append(c)
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break
    if not found:
        return (
            f"No encontré ningún canal llamado #{needle}. Probá "
            "`list_slack_channels` para ver los accesibles."
        )
    if len(found) == 1:
        c = found[0]
        return (
            f"Canal #{needle} -> id={c.get('id')} "
            f"(private={c.get('is_private', False)}, "
            f"members={c.get('num_members', '?')})"
        )
    # Multiple matches (rare, but private vs public can collide on name).
    lines = [
        f"- id={c.get('id')} private={c.get('is_private', False)}"
        for c in found
    ]
    return f"Múltiples canales llamados #{needle}:\n" + "\n".join(lines)


register(
    Tool(
        name="find_slack_channel",
        description=(
            "Resolve a Slack channel name to its id. Acepta '#general' "
            "o 'general'. Antes de leer historial de un canal por "
            "nombre, usá esto para obtener el C-id."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Channel name with or without leading '#'.",
                }
            },
            "required": ["name"],
        },
        handler=_find_slack_channel,
    )
)
