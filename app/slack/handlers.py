"""Slack handlers. Route DMs, group DMs, @mentions, and thread follow-ups to the
agent runner (each in its own task, concurrently), and handle the approval-gate
buttons. No agent logic here, that lives in app/agent."""

from __future__ import annotations

import asyncio
import json
import re

import structlog
from slack_bolt.app.async_app import AsyncApp

from app.agent.runner import resume_run, run_agent

log = structlog.get_logger(__name__)

# Strong refs to in-flight tasks (prevents GC). Each Slack event runs in its own
# task so a slow run never blocks the Socket Mode receive loop (concurrency).
_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)

    def _log_exc(t: asyncio.Task) -> None:
        if not t.cancelled() and t.exception() is not None:
            log.error("handler_task_failed", error=str(t.exception()))

    task.add_done_callback(_log_exc)


_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")


def _clean(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


async def _decide(client, body: dict, decision: str) -> None:
    """Replace the approval message (removing the buttons so it can't be clicked
    again), then resume the paused run."""
    ctx = json.loads(body["actions"][0]["value"])
    label = "✅ Aprobado" if decision == "approve" else "❌ Rechazado"
    try:
        await client.chat_update(
            channel=body["channel"]["id"],
            ts=body["message"]["ts"],
            text=label,
            blocks=[{"type": "section", "text": {"type": "mrkdwn",
                "text": f"{label} — acción riesgosa."}}],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("approval_update_failed", error=str(exc))
    await resume_run(client=client, ctx=ctx, decision=decision)


def register_handlers(app: AsyncApp) -> None:
    @app.event("app_mention")
    async def on_mention(event, body, say, client):  # noqa: ANN001
        ts = event["ts"]
        key = event.get("thread_ts") or ts
        _spawn(
            run_agent(
                client=client,
                team_id=body.get("team_id") or event.get("team"),
                slack_user_id=event.get("user"),
                channel=event["channel"],
                user_text=_clean(event.get("text", "")),
                user_ts=ts,
                conversation_key=key,
                reply_thread_ts=key,
                files=event.get("files"),
            )
        )

    @app.event("message")
    async def on_message(event, body, say, client, context):  # noqa: ANN001
        # Skip bots and Slack's bookkeeping subtypes, but allow file_share — that's
        # how the API delivers a user message with attachments.
        if event.get("bot_id"):
            return
        sub = event.get("subtype")
        if sub and sub != "file_share":
            return
        text = event.get("text", "")
        bot_user_id = context.get("bot_user_id")
        if bot_user_id and f"<@{bot_user_id}>" in text:
            return  # handled by app_mention

        channel_type = event.get("channel_type")
        channel = event["channel"]
        ts = event["ts"]
        thread_ts = event.get("thread_ts")
        common = dict(
            client=client,
            team_id=body.get("team_id") or event.get("team"),
            slack_user_id=event.get("user"),
            channel=channel,
            user_text=_clean(text),
            user_ts=ts,
            files=event.get("files"),
        )

        # DMs and group DMs: respond to every message (flat conversation keyed by
        # channel; reply inline unless already inside a thread).
        if channel_type in ("im", "mpim"):
            if thread_ts:
                _spawn(run_agent(**common, conversation_key=thread_ts, reply_thread_ts=thread_ts))
            else:
                _spawn(run_agent(**common, conversation_key=channel, reply_thread_ts=None))
            return

        # Channels: only continue a thread Sebitas already started.
        if channel_type in ("channel", "group"):
            if not thread_ts:
                return
            _spawn(
                run_agent(
                    **common,
                    conversation_key=thread_ts,
                    reply_thread_ts=thread_ts,
                    require_existing_thread=True,
                )
            )

    # --- Approval gate buttons (human-in-the-loop) ---

    @app.action("agent_approve")
    async def on_approve(ack, body, client):  # noqa: ANN001
        await ack()
        _spawn(_decide(client, body, "approve"))

    @app.action("agent_deny")
    async def on_deny(ack, body, client):  # noqa: ANN001
        await ack()
        _spawn(_decide(client, body, "deny"))
