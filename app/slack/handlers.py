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
from app.concurrency import (
    drain_inbox,
    enqueue_message,
    mark_event_seen,
    try_acquire_thread_lock,
)
from app.slack.skill_commands import (
    handle_skill_file_upload,
    is_skill_upload_pending,
    register_skill_handlers,
)

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
# Mirror of `_looks_like_markdown` in skill_commands.py; kept inline so the
# precursor check in `on_message` stays a cheap predicate.
_MD_EXT_RE = re.compile(r"\.md$", re.IGNORECASE)
_MD_MIMES = {"text/markdown", "text/x-markdown", "text/plain"}


def _clean(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


def _looks_like_md(f: dict) -> bool:
    if f.get("filetype") == "markdown":
        return True
    if (f.get("mimetype") or "") in _MD_MIMES:
        return True
    return bool(_MD_EXT_RE.search(f.get("name") or ""))


def _coalesce(queued: list[dict], current: dict) -> dict:
    """Merge queued payloads + the current trigger into a single run_agent
    invocation. Texts joined oldest-first with `\\n---\\n` so the model can
    see distinct user turns; files are unioned. `user_ts` stays the current
    (latest) so the reaction / reply lands on the most recent message."""
    if not queued:
        return current
    texts = [q.get("user_text") or "" for q in queued] + [current.get("user_text") or ""]
    files: list[dict] = []
    for q in queued:
        qf = q.get("files")
        if qf:
            files.extend(qf)
    cf = current.get("files")
    if cf:
        files.extend(cf)
    out = dict(current)
    out["user_text"] = "\n---\n".join(t for t in texts if t)
    out["files"] = files or None
    return out


async def _route_message(
    *,
    client,
    team_id: str | None,
    event_id: str | None,
    conv_key: str,
    payload: dict,
) -> None:
    """The common entry path: dedupe by event_id, try the per-thread mutex,
    and either spawn the agent (draining queued items first) or enqueue this
    message for the active holder to coalesce later."""
    if event_id and not await mark_event_seen(event_id):
        log.info("slack_event_duplicate", event_id=event_id)
        return
    if not team_id:
        return

    handle = await try_acquire_thread_lock(team_id, conv_key)
    if handle is None:
        await enqueue_message(team_id, conv_key, payload)
        log.info("message_queued", team_id=team_id, conv_key=conv_key)
        return

    queued = await drain_inbox(team_id, conv_key)
    coalesced = _coalesce(queued, payload)
    _spawn(
        run_agent(
            client=client,
            team_id=team_id,
            conversation_key=conv_key,
            lock_handle=handle,
            **coalesced,
        )
    )


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
        await _route_message(
            client=client,
            team_id=body.get("team_id") or event.get("team"),
            event_id=body.get("event_id"),
            conv_key=key,
            payload=dict(
                slack_user_id=event.get("user"),
                channel=event["channel"],
                user_text=_clean(event.get("text", "")),
                user_ts=ts,
                reply_thread_ts=key,
                files=event.get("files"),
            ),
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

        # Skill upload precursor interception: if the user ran `/sebitas skill
        # upload` within the last 5 min AND this message brings a `.md` file,
        # we process it as a skill (not as agent content) and stop here. The
        # skill_commands module decides whether the file looks like markdown.
        files = event.get("files") or []
        team_id_pre = body.get("team_id") or event.get("team")
        user_pre = event.get("user")
        if (
            sub == "file_share"
            and team_id_pre
            and user_pre
            and is_skill_upload_pending(team_id_pre, user_pre)
            and any(_looks_like_md(f) for f in files)
        ):
            md = next(f for f in files if _looks_like_md(f))
            _spawn(handle_skill_file_upload(
                client=client,
                team_id=team_id_pre,
                slack_user_id=user_pre,
                channel=event["channel"],
                file_obj=md,
                thread_ts=event.get("thread_ts"),
            ))
            return

        channel_type = event.get("channel_type")
        channel = event["channel"]
        ts = event["ts"]
        thread_ts = event.get("thread_ts")
        team_id = body.get("team_id") or event.get("team")
        event_id = body.get("event_id")
        base_payload = dict(
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
                await _route_message(
                    client=client, team_id=team_id, event_id=event_id, conv_key=thread_ts,
                    payload={**base_payload, "reply_thread_ts": thread_ts},
                )
            else:
                await _route_message(
                    client=client, team_id=team_id, event_id=event_id, conv_key=channel,
                    payload={**base_payload, "reply_thread_ts": None},
                )
            return

        # Channels: only continue a thread Sebitas already started.
        if channel_type in ("channel", "group"):
            if not thread_ts:
                return
            await _route_message(
                client=client, team_id=team_id, event_id=event_id, conv_key=thread_ts,
                payload={**base_payload, "reply_thread_ts": thread_ts, "require_existing_thread": True},
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

    # --- No-op handlers for URL-style buttons. Slack fires block_actions on
    # every URL button click (for tracking) even though the link is handled
    # client-side. Without a handler Bolt logs "Unhandled request" for each
    # such click. We just ack and do nothing.

    @app.action("connect_url_button")
    async def on_connect_url_click(ack):  # noqa: ANN001
        await ack()

    # /sebitas slash command + skill install/uninstall actions + edit modal.
    # Kept in its own module since it owns its own in-memory state (preview
    # cache + precursor pending) and doesn't share routing with anything else.
    register_skill_handlers(app)
