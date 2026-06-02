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

# Short phrases that ask "are you still working / how's it going" mid-task.
# Matched ONLY when the thread already has an active run; otherwise the text
# goes through the normal agent path so a one-off "como vas?" doesn't get a
# canned response when there's nothing in flight.
_STATUS_QUERY_RE = re.compile(
    r"^\s*"
    r"(c[oó]mo vas|c[oó]mo va|c[oó]mo estamos|c[oó]mo va eso|"
    r"qu[eé] est[aá]s haciendo|qu[eé] hac[eé]s|qu[eé] tal va|"
    r"qu[eé] progreso|progreso\??|update\??|"
    r"actualiz[aá]me|sigues ah[ií]|segu[ií]s ah[ií]|"
    r"sigues vivo|segu[ií]s vivo|on it\??|"
    r"ya casi|c[oó]mo va el avance)"
    r"\s*[.?!]*\s*$",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


def _is_pure_status_query(text: str) -> bool:
    """True when the message is *just* asking for a status check. Anything
    with extra content (a follow-up task, additional context) falls through
    to the normal queueing path so the agent processes it after the active
    run finishes."""
    return bool(_STATUS_QUERY_RE.match(text or ""))


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


async def _respond_with_status(
    *,
    client,
    team_id: str,
    conv_key: str,
    channel: str | None,
    thread_ts: str | None,
) -> None:
    """Status-query path: read the langgraph state for the active run on this
    thread and post a conversational summary. Does NOT touch the run or the
    thread mutex; safe to call concurrently with the active run."""
    if not channel:
        return
    # Lazy import to avoid a runner -> handlers cycle at module load.
    from app.agent.runner import get_active_run_status
    try:
        status = await get_active_run_status(team_id, conv_key)
    except Exception as exc:  # noqa: BLE001
        log.warning("status_query_render_failed", error=str(exc)[:200])
        status = "¡Ya casi! Sigo en eso, en un toque te respondo."
    try:
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text=status,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("status_query_post_failed", error=str(exc)[:200])


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
        # Active run on this thread. If the user is just asking "como vas?"
        # we peek the langgraph state and reply now WITHOUT queueing; the
        # active run keeps going. Anything richer than a status query gets
        # queued and processed after the active run finishes (so the user's
        # follow-up plan + the running task land in the right order).
        if _is_pure_status_query(payload.get("user_text", "")):
            _spawn(_respond_with_status(
                client=client,
                team_id=team_id,
                conv_key=conv_key,
                channel=payload.get("channel"),
                thread_ts=payload.get("reply_thread_ts") or conv_key,
            ))
            return
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


async def _handle_feedback(client, body: dict, *, positive: bool) -> None:
    """Record a 👍 / 👎 user-satisfaction score against the Langfuse trace
    of the run the buttons were attached to. Then replace the footer with
    a quiet acknowledgement so the user has visible confirmation and
    can't double-vote (the replacement has no actions block)."""
    from app.agent.runner import record_feedback_score

    raw = body["actions"][0].get("value") or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}
    trace_id = payload.get("trace_id") or ""
    slack_user_id = (body.get("user") or {}).get("id")

    await record_feedback_score(
        trace_id=trace_id,
        value=1.0 if positive else 0.0,
        slack_user_id=slack_user_id,
    )

    # Publish a user_satisfaction_low event on 👎 so any automation
    # subscribed to it can fire (e.g. "DM Sam when someone marks 👎").
    # Best-effort: a failing publish must not block the ack flow.
    if not positive:
        try:
            from app.automations.events import (
                Event as _AutoEvent,
                current_fire_depth as _depth,
                publish as _publish,
            )
            from app.db import repository as _repo
            from app.db.session import get_session as _get_session

            team_id = (body.get("team") or {}).get("id")
            if team_id:
                async with _get_session() as _session:
                    ws = await _repo.get_workspace(_session, team_id)
                if ws is not None:
                    await _publish(
                        _AutoEvent(
                            type="user_satisfaction_low",
                            workspace_id=ws.id,
                            data={
                                "trace_id": trace_id,
                                "slack_user_id": slack_user_id or "",
                            },
                            fire_depth=_depth(),
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            log.warning("automation_publish_satisfaction_failed", error=str(exc))

    ack_text = (
        "👍 Bien, anotado."
        if positive
        else "👎 Anotado, voy a revisar dónde fallé."
    )
    try:
        await client.chat_update(
            channel=body["channel"]["id"],
            ts=body["message"]["ts"],
            text=ack_text,
            blocks=[
                {"type": "context", "elements": [
                    {"type": "mrkdwn", "text": f"_{ack_text}_"}
                ]}
            ],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("feedback_update_failed", error=str(exc))


async def _decide(client, body: dict, decision: str) -> None:
    """Replace the approval message (removing the buttons so it can't be clicked
    again), then resume the paused run. The replacement message carries no
    actions block, so a double-click on the original is a no-op."""
    ctx = json.loads(body["actions"][0]["value"])
    if decision == "approve":
        text = ":white_check_mark: *Aprobado.* Sigo con la tarea."
    else:
        text = ":x: *Rechazado.* No hice nada."
    try:
        await client.chat_update(
            channel=body["channel"]["id"],
            ts=body["message"]["ts"],
            text="Aprobado." if decision == "approve" else "Rechazado.",
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
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

        # Skill upload interception. Two trigger paths, both accepted:
        # (a) Precursor: user ran `/misterr skill upload` within the last 5 min.
        # (b) Natural language: user sent a `.md` in a DM with the bot (no
        #     precursor required). The slash command path is fragile across
        #     workspaces (manifest propagation), the DM-with-md path is robust
        #     because it only depends on bot DMs working, which they do
        #     wherever the app is installed. The preview Block Kit still gates
        #     the actual install with Install/Edit/Cancel buttons, so an
        #     accidental .md attached in a DM is one cancel-click away.
        files = event.get("files") or []
        team_id_pre = body.get("team_id") or event.get("team")
        user_pre = event.get("user")
        channel_type_pre = event.get("channel_type")
        has_md = sub == "file_share" and any(_looks_like_md(f) for f in files)
        is_dm = channel_type_pre == "im"
        precursor_pending = bool(
            team_id_pre and user_pre
            and is_skill_upload_pending(team_id_pre, user_pre)
        )
        if (
            has_md
            and team_id_pre
            and user_pre
            and (precursor_pending or is_dm)
        ):
            # Spawn one task per .md file. Each gets its own preview ephemeral
            # so the user can independently install / edit / cancel each. The
            # precursor is consumed by the first task; subsequent tasks see
            # it cleared but proceed because we already gated at intake.
            md_files = [f for f in files if _looks_like_md(f)]
            for md in md_files:
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

        # 1:1 DM (channel_type='im'): respond to every message. The DM is
        # the user-to-bot channel by definition, so opt-in is implicit.
        if channel_type == "im":
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

        # Group DMs (mpim) AND channels: only continue a thread Misterr
        # already participates in. @-mentions are handled by the
        # `app_mention` event above (short-circuited at the top of this
        # handler). Without this restriction, Misterr used to reply to
        # every message in an MPIM, which felt intrusive to the other
        # humans in the group chat.
        if channel_type in ("mpim", "channel", "group"):
            if not thread_ts:
                return
            await _route_message(
                client=client, team_id=team_id, event_id=event_id, conv_key=thread_ts,
                payload={**base_payload, "reply_thread_ts": thread_ts, "require_existing_thread": True},
            )

    # --- Bot was added to a channel: deep-scan its history so future
    # responses in this channel have context. Fire-and-forget; the scan
    # writes observations to a per-channel memory skill (`channels/<id>`)
    # so the agent picks them up only when responding IN that channel,
    # not workspace-wide.
    @app.event("member_joined_channel")
    async def on_member_joined(event, body, context):  # noqa: ANN001
        joined_user = event.get("user")
        bot_user_id = context.get("bot_user_id")
        # Only fire when WE are the one joining. Other people joining a
        # channel Misterr is in: no action.
        if not joined_user or not bot_user_id or joined_user != bot_user_id:
            return
        team_id = body.get("team_id") or event.get("team")
        channel_id = event.get("channel")
        if not team_id or not channel_id:
            return
        try:
            from app.memory.onboarding import scan_single_channel
            from app.db import repository as _repo
            from app.db.session import get_session

            async with get_session() as session:
                ws = await _repo.get_workspace(session, team_id)
            if ws is None:
                log.warning(
                    "on_join_scan_skipped_no_workspace",
                    team_id=team_id, channel_id=channel_id,
                )
                return
            log.info(
                "on_join_scan_spawn",
                workspace_id=str(ws.id), channel_id=channel_id,
            )
            _spawn(scan_single_channel(ws.id, channel_id))
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "on_join_scan_spawn_failed",
                team_id=team_id, channel_id=channel_id,
                error=str(exc)[:200],
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

    @app.action("agent_feedback_up")
    async def on_feedback_up(ack, body, client):  # noqa: ANN001
        await ack()
        _spawn(_handle_feedback(client, body, positive=True))

    @app.action("agent_feedback_down")
    async def on_feedback_down(ack, body, client):  # noqa: ANN001
        await ack()
        _spawn(_handle_feedback(client, body, positive=False))

    # --- No-op handlers for URL-style buttons. Slack fires block_actions on
    # every URL button click (for tracking) even though the link is handled
    # client-side. Without a handler Bolt logs "Unhandled request" for each
    # such click. We just ack and do nothing.

    @app.action("connect_url_button")
    async def on_connect_url_click(ack):  # noqa: ANN001
        await ack()

    # /misterr slash command + skill install/uninstall actions + edit modal.
    # Kept in its own module since it owns its own in-memory state (preview
    # cache + precursor pending) and doesn't share routing with anything else.
    register_skill_handlers(app)
