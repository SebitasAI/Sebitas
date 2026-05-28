"""Drives an agent run for a Slack conversation, non-blocking.

Flow: reconstruct thread history -> run the LangGraph agent -> if it pauses for
approval (risky tool), post Slack buttons and return (state is checkpointed);
on the button click resume the graph -> when it finishes, persist the turns and
post the reply in the thread. Designed to be moved behind a worker/queue
(Temporal) later: run_agent / resume_run are self-contained and take plain args.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from langfuse import get_client, propagate_attributes
from langgraph.types import Command

from app.agent.context import set_run_context
from app.agent.graph import get_graph
from app.agent.sandbox import close_run_sandbox
from app.db import repository as repo
from app.db.session import get_session
from app.skills.registry import installed_descriptions_text

log = structlog.get_logger(__name__)
_langfuse = get_client()

_THINKING_REACTION = "hourglass_flowing_sand"


async def _add_reaction(client, channel: str, ts: str) -> None:
    try:
        await client.reactions_add(channel=channel, timestamp=ts, name=_THINKING_REACTION)
    except Exception as exc:  # noqa: BLE001
        log.warning("reaction_add_failed", error=str(exc))


async def _remove_reaction(client, channel: str, ts: str) -> None:
    try:
        await client.reactions_remove(channel=channel, timestamp=ts, name=_THINKING_REACTION)
    except Exception as exc:  # noqa: BLE001
        log.warning("reaction_remove_failed", error=str(exc))


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #

async def _load_history(team_id: str, channel: str, conversation_key: str) -> list[dict]:
    """Prior user/assistant text turns of the thread, for multi-turn context."""
    async with get_session() as session:
        workspace = await repo.get_workspace(session, team_id)
        if workspace is None:
            return []
        thread = await repo.get_thread(session, workspace.id, channel, conversation_key)
        if thread is None:
            return []
        rows = await repo.get_thread_messages(session, thread.id, limit=30)
    history: list[dict] = []
    for m in rows:
        if m.role in ("user", "assistant") and m.tool_calls is None and m.text:
            if history and history[-1]["role"] == m.role:
                history[-1]["content"] += "\n" + m.text
            else:
                history.append({"role": m.role, "content": m.text})
    return history


async def _persist_user(team_id: str, channel: str, conversation_key: str, slack_user_id: str, text: str, ts: str) -> uuid.UUID:
    async with get_session() as session:
        workspace = await repo.upsert_workspace(session, team_id)
        user = await repo.upsert_app_user(session, workspace.id, slack_user_id)
        thread = await repo.get_or_create_thread(session, workspace.id, channel, conversation_key)
        await repo.add_message(
            session, thread.id, role="user", text=text, app_user_id=user.id, slack_ts=ts
        )
        await session.commit()
        return workspace.id


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


async def _persist_run_messages(team_id: str, channel: str, conversation_key: str, run_messages: list[dict]) -> None:
    """Persist assistant turns (with tool_calls) and tool results from a run."""
    try:
        async with get_session() as session:
            workspace = await repo.get_workspace(session, team_id)
            if workspace is None:
                return
            thread = await repo.get_thread(session, workspace.id, channel, conversation_key)
            if thread is None:
                return
            for m in run_messages:
                content = m.get("content")
                if m.get("role") == "assistant":
                    tool_calls = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"] if isinstance(content, list) else []
                    await repo.add_message(
                        session, thread.id, role="assistant",
                        text=_text_of(content),
                        tool_calls=tool_calls or None,
                    )
                elif m.get("role") == "user" and isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            await repo.add_message(
                                session, thread.id, role="tool",
                                text=str(b.get("content", "")),
                                tool_call_id=b.get("tool_use_id"),
                            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("persist_run_messages_failed", error=str(exc))


# --------------------------------------------------------------------------- #
# Slack output
# --------------------------------------------------------------------------- #

async def _post_approval(client, ctx: dict, payload: dict) -> None:
    tools = payload.get("tools", [])
    lines = "\n".join(f"• `{t['name']}` con `{json.dumps(t['input'], ensure_ascii=False)}`" for t in tools)
    value = json.dumps(ctx)
    await client.chat_postMessage(
        channel=ctx["channel"],
        thread_ts=ctx.get("reply_thread_ts"),
        text="Aprobación requerida para una acción riesgosa.",
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f":warning: *Sebitas quiere ejecutar una acción riesgosa:*\n{lines}"}},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Aprobar"},
                 "style": "primary", "action_id": "agent_approve", "value": value},
                {"type": "button", "text": {"type": "plain_text", "text": "Rechazar"},
                 "style": "danger", "action_id": "agent_deny", "value": value},
            ]},
        ],
    )


async def _drive(client, ctx: dict, result: dict) -> None:
    """Handle a graph result: pause for approval/connect, or finish (persist + reply)."""
    interrupts = result.get("__interrupt__")
    if interrupts:
        payload = interrupts[0].value
        if isinstance(payload, dict) and payload.get("type") == "connect":
            from app.integrations import connect  # lazy: avoid import cycle
            await connect.start_connect(client, ctx, payload.get("app", ""))
        else:
            await _post_approval(client, ctx, payload)
        return  # state is checkpointed; resumes on the button click

    messages = result.get("messages", [])
    run_messages = messages[ctx.get("seed_len", 0):]
    await _persist_run_messages(ctx["team_id"], ctx["channel"], ctx["conversation_key"], run_messages)

    final = ""
    for m in reversed(messages):
        if m.get("role") == "assistant":
            final = _text_of(m.get("content"))
            if final:
                break
    await client.chat_postMessage(
        channel=ctx["channel"], thread_ts=ctx.get("reply_thread_ts"), text=final or "(sin respuesta)"
    )
    await _remove_reaction(client, ctx["channel"], ctx["user_ts"])
    await close_run_sandbox(ctx["run_id"])  # run finished -> tear down its sandbox


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #

async def run_agent(*, client, team_id: str | None, slack_user_id: str | None, channel: str,
                    user_text: str, user_ts: str, conversation_key: str, reply_thread_ts: str | None,
                    require_existing_thread: bool = False) -> None:
    text = user_text.strip()
    if not text or not team_id or not slack_user_id:
        return

    # Channel follow-ups: only continue a thread Sebitas already started.
    if require_existing_thread:
        async with get_session() as session:
            workspace = await repo.get_workspace(session, team_id)
            if workspace is None or await repo.get_thread(session, workspace.id, channel, conversation_key) is None:
                return

    await _add_reaction(client, channel, user_ts)
    history = await _load_history(team_id, channel, conversation_key)
    workspace_id = await _persist_user(team_id, channel, conversation_key, slack_user_id, text, user_ts)

    seed = history + [{"role": "user", "content": text}]
    run_id = f"{conversation_key}:{user_ts}"
    # Tenancy context for the run: the sandbox and skills scope to this workspace,
    # and the installed-skills list is surfaced to the model (progressive loading).
    skills_context = await installed_descriptions_text(workspace_id)
    set_run_context(workspace_id=str(workspace_id), run_id=run_id, skills_context=skills_context)
    ctx = {
        "run_id": run_id, "seed_len": len(seed), "team_id": team_id,
        "workspace_id": str(workspace_id),
        "channel": channel, "conversation_key": conversation_key,
        "reply_thread_ts": reply_thread_ts, "user_ts": user_ts,
    }
    config = {"configurable": {"thread_id": run_id}}

    with _langfuse.start_as_current_observation(
        as_type="span", name="agent-run", input={"text": text}
    ), propagate_attributes(
        session_id=f"{team_id}:{channel}:{conversation_key}",
        user_id=slack_user_id, tags=["slack", "agent"], metadata={"tenant": team_id},
    ):
        result = await get_graph().ainvoke({"messages": seed, "iterations": 0}, config)
        await _drive(client, ctx, result)


async def resume_run(*, client, ctx: dict, decision: str) -> None:
    """Resume a paused run after an approval decision ('approve' | 'deny')."""
    config = {"configurable": {"thread_id": ctx["run_id"]}}
    # Idempotency: if the run has no pending step (already resumed/finished),
    # do nothing. Covers rapid double-clicks before the message is updated.
    snapshot = await get_graph().aget_state(config)
    if not snapshot.next:
        log.info("resume_skipped", run_id=ctx["run_id"], reason="no_pending_interrupt")
        return

    # Restore tenancy context for the resumed portion (this is a new task).
    async with get_session() as session:
        workspace = await repo.get_workspace(session, ctx["team_id"])
    if workspace is not None:
        set_run_context(
            workspace_id=str(workspace.id),
            run_id=ctx["run_id"],
            skills_context=await installed_descriptions_text(workspace.id),
        )

    with _langfuse.start_as_current_observation(
        as_type="span", name="agent-resume", input={"decision": decision}
    ), propagate_attributes(
        session_id=f"{ctx['team_id']}:{ctx['channel']}:{ctx['conversation_key']}",
        tags=["slack", "agent", "resume"], metadata={"tenant": ctx["team_id"]},
    ):
        result = await get_graph().ainvoke(Command(resume=decision), config)
        await _drive(client, ctx, result)


async def resume_after_connect(ctx: dict) -> None:
    """Resume a run paused waiting for an integration connection. Called by the
    webhook + polling fallback, which don't have a request-bound Slack `client`
    — build one from the bot token. Idempotent via resume_run's snapshot guard."""
    from app.config import get_settings  # local to avoid module-load side effects
    from slack_sdk.web.async_client import AsyncWebClient

    client = AsyncWebClient(token=get_settings().slack_bot_token)
    await resume_run(client=client, ctx=ctx, decision="connected")
