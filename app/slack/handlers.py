"""Generic Slack handlers. Sebitas replies in DMs and group DMs, when @mentioned
in any channel, and as a follow-up inside threads it is already part of. It keeps
per-conversation memory (the thread, or the whole DM) by replaying history to
Claude. Zero domain logic."""

from __future__ import annotations

import re
import time

import structlog
from langfuse import get_client, propagate_attributes
from slack_bolt.app.async_app import AsyncApp

from app.agent.claude import generate_reply
from app.db import repository as repo
from app.db.session import get_session

log = structlog.get_logger(__name__)
_langfuse = get_client()

# Strips leading/inline <@U123> mention tokens from the text.
_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")

# Reaction shown on the user's message while Sebitas is thinking.
_THINKING_REACTION = "hourglass_flowing_sand"


async def _add_reaction(client, channel: str, ts: str) -> bool:
    """Add the thinking reaction. Non-fatal: a missing 'reactions:write' scope
    (or an already-reacted message) must not break the reply."""
    try:
        await client.reactions_add(channel=channel, timestamp=ts, name=_THINKING_REACTION)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("reaction_add_failed", error=str(exc))
        return False


async def _remove_reaction(client, channel: str, ts: str) -> None:
    try:
        await client.reactions_remove(
            channel=channel, timestamp=ts, name=_THINKING_REACTION
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("reaction_remove_failed", error=str(exc))


def _to_history(messages) -> list[dict[str, str]]:
    """Turn stored Message rows into Claude's messages list, merging consecutive
    same-role turns so the sequence strictly alternates (starts with a user turn)."""
    history: list[dict[str, str]] = []
    for m in messages:
        if history and history[-1]["role"] == m.role:
            history[-1]["content"] += "\n" + m.text
        else:
            history.append({"role": m.role, "content": m.text})
    return history


async def _process(
    *,
    client,
    team_id: str | None,
    slack_user_id: str | None,
    channel: str,
    text: str,
    ts: str,
    conversation_key: str,
    reply_thread_ts: str | None,
    say,
    require_existing_thread: bool = False,
) -> None:
    """Persist the user message, reply with Claude (with conversation memory),
    then persist the reply. `conversation_key` is the slack_thread_ts used to
    group a conversation: a real thread ts, or the channel id for a flat DM."""
    clean = _MENTION_RE.sub("", text or "").strip()
    if not clean or not team_id or not slack_user_id:
        return

    # Channel follow-ups: only continue conversations Sebitas already started.
    # Checked before reacting so we never touch unrelated threads.
    if require_existing_thread:
        async with get_session() as session:
            workspace = await repo.get_workspace(session, team_id)
            if workspace is None:
                return
            existing = await repo.get_thread(
                session, workspace.id, channel, conversation_key
            )
            if existing is None:
                return

    # Hourglass on the user's message while thinking; removed in finally.
    perf = time.perf_counter
    t_start = perf()
    reacted = await _add_reaction(client, channel, ts)
    t_reacted = perf()
    try:
        with _langfuse.start_as_current_observation(
            as_type="span", name="slack-message", input={"text": clean}
        ) as root, propagate_attributes(
            session_id=f"{team_id}:{channel}:{conversation_key}",
            user_id=slack_user_id,
            tags=["slack"],
            metadata={"tenant": team_id},
        ):
            # Persist the inbound user message, then load conversation history.
            async with get_session() as session:
                workspace = await repo.upsert_workspace(session, team_id)
                user = await repo.upsert_app_user(session, workspace.id, slack_user_id)
                thread = await repo.get_or_create_thread(
                    session, workspace.id, channel, conversation_key
                )
                await repo.add_message(
                    session,
                    thread.id,
                    role="user",
                    text=clean,
                    app_user_id=user.id,
                    slack_ts=ts,
                )
                await session.commit()
                thread_id = thread.id
                history = _to_history(
                    await repo.get_thread_messages(session, thread_id)
                )
            t_db_user = perf()

            reply = await generate_reply(history)
            t_model = perf()

            # Persist the assistant reply (no app_user).
            async with get_session() as session:
                await repo.add_message(
                    session, thread_id, role="assistant", text=reply
                )
                await session.commit()
            t_db_asst = perf()

            root.update(output=reply)

        await say(text=reply, thread_ts=reply_thread_ts)
        t_say = perf()
        log.info(
            "timing",
            react_s=round(t_reacted - t_start, 2),
            db_user_s=round(t_db_user - t_reacted, 2),
            model_s=round(t_model - t_db_user, 2),
            db_asst_s=round(t_db_asst - t_model, 2),
            say_s=round(t_say - t_db_asst, 2),
            total_s=round(t_say - t_start, 2),
        )
    finally:
        if reacted:
            await _remove_reaction(client, channel, ts)


def register_handlers(app: AsyncApp) -> None:
    @app.event("app_mention")
    async def on_mention(event, body, say, client):  # noqa: ANN001
        # A mention lands in a channel root or inside a thread; reply threaded.
        ts = event["ts"]
        key = event.get("thread_ts") or ts
        await _process(
            client=client,
            team_id=body.get("team_id") or event.get("team"),
            slack_user_id=event.get("user"),
            channel=event["channel"],
            text=event.get("text", ""),
            ts=ts,
            conversation_key=key,
            reply_thread_ts=key,
            say=say,
        )

    @app.event("message")
    async def on_message(event, body, say, client, context):  # noqa: ANN001
        # Ignore bot echoes and edited/system subtypes.
        if event.get("subtype") or event.get("bot_id"):
            return

        text = event.get("text", "")
        # Mentions are handled by app_mention; skip here to avoid double replies.
        bot_user_id = context.get("bot_user_id")
        if bot_user_id and f"<@{bot_user_id}>" in text:
            return

        channel_type = event.get("channel_type")
        channel = event["channel"]
        ts = event["ts"]
        thread_ts = event.get("thread_ts")
        common = dict(
            client=client,
            team_id=body.get("team_id") or event.get("team"),
            slack_user_id=event.get("user"),
            channel=channel,
            text=text,
            ts=ts,
            say=say,
        )

        # DMs and group DMs: respond to every message. The whole DM is one
        # conversation (keyed by channel) so memory persists; reply inline unless
        # the message is itself inside a thread.
        if channel_type in ("im", "mpim"):
            if thread_ts:
                await _process(
                    **common, conversation_key=thread_ts, reply_thread_ts=thread_ts
                )
            else:
                await _process(
                    **common, conversation_key=channel, reply_thread_ts=None
                )
            return

        # Public/private channels: only continue a thread Sebitas already started.
        # Initial engagement in a channel happens via @mention (app_mention).
        if channel_type in ("channel", "group"):
            if not thread_ts:
                return
            await _process(
                **common,
                conversation_key=thread_ts,
                reply_thread_ts=thread_ts,
                require_existing_thread=True,
            )
