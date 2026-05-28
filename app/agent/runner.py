"""Drives an agent run for a Slack conversation, non-blocking.

Flow: reconstruct thread history -> run the LangGraph agent -> if it pauses for
approval (risky tool), post Slack buttons and return (state is checkpointed);
on the button click resume the graph -> when it finishes, persist the turns and
post the reply in the thread. Designed to be moved behind a worker/queue
(Temporal) later: run_agent / resume_run are self-contained and take plain args.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import structlog
from langfuse import get_client, propagate_attributes
from langgraph.types import Command

from app.agent.context import set_run_context
from app.agent.graph import get_graph
from app.agent.sandbox import close_run_sandbox
from app.concurrency import (
    ThreadLockHandle,
    drain_inbox,
    try_acquire_thread_lock,
)
from app.config import get_settings
from app.db import repository as repo
from app.db.session import get_session
from app.skills.prompt_builder import build_skills_context
from app.slack import mentions as _mentions
from app.slack import roster as _roster

log = structlog.get_logger(__name__)
_langfuse = get_client()

_THINKING_REACTION = "hourglass_flowing_sand"


async def _process_youtube_links(
    text: str, client, channel: str, thread_ts: str | None,
) -> tuple[str, list[dict], list[str]]:
    """For each unique YouTube URL in `text`, fetch captions + oEmbed metadata
    and produce (text_prepend, attachments_records, unsupported_msgs).

    The text_prepend chunk goes into the agent's first user turn (prepended
    to their message). Attachments records get persisted on the user message.
    Unsupported are surfaced to Slack as a polite list."""
    from app.slack import youtube as _yt

    refs = _yt.extract_video_ids(text)
    if not refs:
        return "", [], []

    settings = get_settings()
    max_chars = settings.attachment_max_text_chars

    # Status update so the user sees we're working on YT (each call is a few seconds).
    try:
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f":mag: Bajando transcript de YouTube ({len(refs)} link(s))...",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("yt_status_post_failed", error=str(exc))

    text_blocks: list[str] = []
    records: list[dict] = []
    unsupported: list[str] = []
    for ref in refs:
        vid = ref["video_id"]
        try:
            tr = await _yt.fetch_transcript(vid)
        except _yt.NoCaptionsError:
            unsupported.append(
                f"`{vid}` no tiene captions disponibles. Mandalo como archivo de video "
                "o pedile al creador que active subtítulos."
            )
            continue
        except Exception as exc:  # noqa: BLE001
            log.warning("yt_transcript_failed", video_id=vid, error=str(exc))
            unsupported.append(f"`{vid}`: {exc}")
            continue
        meta = await _yt.fetch_metadata(vid)
        full = tr["transcript"]
        truncated_marker = ""
        if len(full) > max_chars:
            full = full[:max_chars].rstrip() + "…"
            truncated_marker = f" (truncado a {max_chars} chars del transcript original)"
            unsupported.append(
                f"`{vid}` el transcript es muy largo; mandando los primeros "
                f"~{max_chars // 1000}K chars."
            )
        # Build the header bits.
        bits = ["YouTube transcript"]
        if meta.get("title"):
            bits.append(f"de «{meta['title']}»")
        if meta.get("channel"):
            bits.append(f"por {meta['channel']}")
        if tr.get("duration_s"):
            bits.append(f"~{int(tr['duration_s'])}s")
        text_blocks.append(f"[{', '.join(bits)}{truncated_marker}]:\n{full}")

        records.append({
            "slack_file_id": vid,  # used as a stable id within (message_id, slack_file_id)
            "mime_type": "application/x-youtube-link",
            "r2_ref": None,
            "original_name": meta.get("title") or vid,
            "size_bytes": len(tr["transcript"]),
            "transcript": tr["transcript"],
            "attachment_type": "youtube_link",
            "attachment_metadata": {
                "video_id": vid,
                "url": ref["url"],
                "title": meta.get("title"),
                "channel": meta.get("channel"),
                "channel_url": meta.get("channel_url"),
                "duration_s": tr.get("duration_s"),
                "language": tr.get("language"),
                "segments_count": tr.get("segments_count"),
            },
        })
        log.info(
            "youtube_transcript_fetched",
            video_id=vid, duration_s=tr.get("duration_s"),
            chars=len(tr["transcript"]),
        )
    return "\n\n".join(text_blocks), records, unsupported


async def _build_channel_roster_block(workspace_id: uuid.UUID, channel: str | None) -> str:
    """Compact members list (max 50) for the current channel, surfaced to the
    model as an uncached system block so it can use real <@U...> mentions
    without a tool round-trip. Empty when channel is missing or roster sync
    fails -- the agent falls back to find_slack_user."""
    if not channel:
        return ""
    try:
        members = await _roster.get_channel_members(workspace_id, channel, limit=50)
    except Exception as exc:  # noqa: BLE001
        log.warning("roster_channel_failed", channel=channel, error=str(exc))
        return ""
    if not members:
        return ""
    lines = [
        f"• {m.get('display_name') or m.get('real_name') or '?'}"
        f"{' [app]' if m.get('is_bot') else ''} — <@{m['slack_user_id']}>"
        for m in members
    ]
    suffix = (
        "\n(More members exist; call find_slack_user(query) for anyone not listed above.)"
        if len(members) >= 50
        else ""
    )
    return (
        f"Channel members for <#{channel}> (use these <@U...> ids "
        f"when mentioning):\n" + "\n".join(lines) + suffix
    )


async def _render_outbound(text: str, ctx: dict) -> str:
    """Run agent-generated text through the mentions post-processor before
    posting to Slack. Converts `@name` -> `<@U...>`, blocks `@here`/etc., and
    resolves `#channel` -> `<#C...|name>`. Safety net for the system prompt."""
    if not text:
        return text
    ws_str = ctx.get("workspace_id")
    ws_uuid = uuid.UUID(ws_str) if ws_str else None
    return await _mentions.render_for_slack(text, workspace_id=ws_uuid, channel_id=ctx.get("channel"))


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
    """Prior user/assistant turns of the thread, for multi-turn context. User
    messages with persisted attachments are re-hydrated from R2 into Anthropic
    content blocks (image/document via fresh presigned URL, text inline)."""
    from app.slack import files as sf  # lazy: avoid import cycle at module load

    async with get_session() as session:
        workspace = await repo.get_workspace(session, team_id)
        if workspace is None:
            return []
        thread = await repo.get_thread(session, workspace.id, channel, conversation_key)
        if thread is None:
            return []
        rows = await repo.get_thread_messages(session, thread.id, limit=30)
        user_msg_ids = [m.id for m in rows if m.role == "user" and m.tool_calls is None]
        attachments_by_msg = await repo.get_attachments_for_messages(session, user_msg_ids)

    settings = get_settings()
    history: list[dict] = []
    for m in rows:
        if m.role not in ("user", "assistant") or m.tool_calls is not None:
            continue
        text = m.text or ""
        if m.role == "user":
            atts = attachments_by_msg.get(m.id, [])
            if atts:
                blocks, prepend = await sf.build_attachment_blocks(atts, settings.attachment_max_text_chars)
                combined = ((prepend + "\n\n" + text) if prepend and text else (prepend or text)) or " "
                content: Any = list(blocks) + [{"type": "text", "text": combined}]
                history.append({"role": "user", "content": content})
                continue
            if not text:
                continue
            if history and history[-1]["role"] == "user" and isinstance(history[-1]["content"], str):
                history[-1]["content"] += "\n" + text
            else:
                history.append({"role": "user", "content": text})
        else:  # assistant
            if not text:
                continue
            if history and history[-1]["role"] == "assistant" and isinstance(history[-1]["content"], str):
                history[-1]["content"] += "\n" + text
            else:
                history.append({"role": "assistant", "content": text})
    return history


async def _ensure_workspace(team_id: str) -> uuid.UUID:
    """Resolve (and create if needed) the workspace id without persisting any
    message. Used by the file-ingest path which needs the R2 prefix before the
    user message can be written (attachments depend on its row)."""
    async with get_session() as session:
        workspace = await repo.upsert_workspace(session, team_id)
        await session.commit()
        return workspace.id


async def _persist_user(
    team_id: str,
    channel: str,
    conversation_key: str,
    slack_user_id: str,
    text: str,
    ts: str,
    attachments: list[dict] | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (workspace_id, app_user_id). The user id is used to scope
    per-user features (Skills) without re-resolving it later in the run."""
    async with get_session() as session:
        workspace = await repo.upsert_workspace(session, team_id)
        user = await repo.upsert_app_user(session, workspace.id, slack_user_id)
        thread = await repo.get_or_create_thread(session, workspace.id, channel, conversation_key)
        message = await repo.add_message(
            session, thread.id, role="user", text=text, app_user_id=user.id, slack_ts=ts
        )
        for a in attachments or []:
            await repo.add_attachment(
                session, message.id,
                slack_file_id=a["slack_file_id"],
                mime_type=a["mime_type"],
                r2_ref=a.get("r2_ref"),
                original_name=a.get("original_name"),
                size_bytes=a.get("size_bytes"),
                transcript=a.get("transcript"),
                attachment_type=a.get("attachment_type", "file"),
                attachment_metadata=a.get("attachment_metadata"),
            )
        await session.commit()
        return workspace.id, user.id


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

def _fmt_params(params: dict, limit: int = 80) -> str:
    """Render tool input params as a short human-friendly suffix. Skips empty
    values; truncates long values; never echoes raw JSON to the user."""
    if not isinstance(params, dict) or not params:
        return ""
    parts = []
    for k, v in params.items():
        s = "" if v is None else str(v)
        if len(s) > limit:
            s = s[:limit].rstrip() + "…"
        parts.append(f"`{k}`=`{s}`" if s else f"`{k}`")
    return " · " + ", ".join(parts) if parts else ""


def _render_tool_call(t: dict) -> str:
    """One bullet line per pending risky tool call. Per-tool rendering so the
    user sees what's about to happen in plain language, not raw JSON."""
    name = t.get("name") or "?"
    inp = t.get("input") if isinstance(t.get("input"), dict) else {}
    if name == "run_action":
        app = inp.get("app", "?")
        action = inp.get("action_id", "?")
        return f"• Ejecutar `{action}` en *{app}*{_fmt_params(inp.get('params') or {})}"
    if name == "disconnect_integration":
        return f"• Desconectar *{inp.get('app', '?')}*"
    if name == "simulate_destructive_action":
        return f"• (demo) acción destructiva sobre `{inp.get('target', '?')}`"
    return f"• `{name}`{_fmt_params(inp)}"


async def _post_preamble_before_gate(client, ctx: dict, result: dict) -> None:
    """If the model emitted text alongside the risky tool_use blocks, post it
    as a normal message before the approval gate so the user has context
    ("voy a hacer X y para qué") before they decide. Best-effort: no preamble
    text -> silent no-op."""
    messages = result.get("messages", [])
    last_assistant = next(
        (m for m in reversed(messages) if m.get("role") == "assistant"), None
    )
    if not last_assistant:
        return
    text = _text_of(last_assistant.get("content")).strip()
    if not text:
        return
    rendered = await _render_outbound(text, ctx)
    try:
        await client.chat_postMessage(
            channel=ctx["channel"], thread_ts=ctx.get("reply_thread_ts"), text=rendered,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("preamble_post_failed", error=str(exc))


async def _post_approval(client, ctx: dict, payload: dict) -> None:
    tools = payload.get("tools", [])
    lines = "\n".join(_render_tool_call(t) for t in tools)
    n = len(tools)
    header = (
        "*Aprobación requerida.* Sebitas quiere ejecutar:"
        if n == 1
        else f"*Aprobación requerida.* Sebitas quiere ejecutar {n} acciones:"
    )
    value = json.dumps(ctx)
    await client.chat_postMessage(
        channel=ctx["channel"],
        thread_ts=ctx.get("reply_thread_ts"),
        text="Aprobación requerida.",
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f":warning: {header}\n{lines}"}},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Aprobar"},
                 "style": "primary", "action_id": "agent_approve", "value": value},
                {"type": "button", "text": {"type": "plain_text", "text": "Rechazar"},
                 "style": "danger", "action_id": "agent_deny", "value": value},
            ]},
        ],
    )


async def _drive(client, ctx: dict, result: dict, *, lock_handle: ThreadLockHandle | None = None) -> None:
    """Handle a graph result: pause for approval/connect, or finish (persist + reply).

    Releases the thread mutex at the right moment: on interrupt we release as
    soon as we know we're pausing (so the next inbound message can enter the
    thread); on end_turn we release after persistence + reply, then schedule a
    debounced drain to process any messages queued during the run."""
    interrupts = result.get("__interrupt__")
    if interrupts:
        # Release the lock BEFORE posting the gate / connect link: pausing
        # the run means another message (e.g. a follow-up after the user
        # finishes a connect flow) should be allowed to enter the thread.
        if lock_handle is not None:
            await lock_handle.release()
        payload = interrupts[0].value
        if isinstance(payload, dict) and payload.get("type") == "connect":
            from app.integrations import connect  # lazy: avoid import cycle
            await connect.start_connect(client, ctx, payload.get("app", ""))
        else:
            # Surface any preamble text the model emitted alongside the risky
            # tool call so the user sees "what and why" before the gate (the
            # system prompt asks for this; also resilient if the model skips).
            await _post_preamble_before_gate(client, ctx, result)
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
    rendered_final = await _render_outbound(final, ctx) if final else "(sin respuesta)"
    await client.chat_postMessage(
        channel=ctx["channel"], thread_ts=ctx.get("reply_thread_ts"), text=rendered_final,
    )
    await _remove_reaction(client, ctx["channel"], ctx["user_ts"])
    await close_run_sandbox(ctx["run_id"])  # run finished -> tear down its sandbox

    # End of turn: release the mutex and kick off a debounced drain of any
    # messages that arrived during this run. Doesn't block: the drain runs
    # as a detached task so this run's response isn't held up.
    if lock_handle is not None:
        await lock_handle.release()
    if ctx.get("team_id") and ctx.get("conversation_key"):
        asyncio.create_task(
            _debounce_drain(client, ctx["team_id"], ctx["channel"], ctx["conversation_key"])
        )


async def _debounce_drain(client, team_id: str, channel: str, conv_key: str) -> None:
    """After a run ends, wait a short window then drain any queued messages
    and run a follow-up turn with them coalesced. The wait absorbs bursts
    ('si si si' typed rapidly) into a single follow-up run."""
    await asyncio.sleep(0.7)
    handle = await try_acquire_thread_lock(team_id, conv_key)
    if handle is None:
        return  # another holder is active; they'll drain when done
    try:
        queued = await drain_inbox(team_id, conv_key)
        if not queued:
            return
        # Build a coalesced run from the queued payloads. Use the LATEST
        # payload as the "current" so reply_thread_ts and reactions land on
        # the most recent user message.
        latest = queued[-1]
        coalesced_text = "\n---\n".join(q.get("user_text") or "" for q in queued if q.get("user_text"))
        files: list[dict] = []
        for q in queued:
            qf = q.get("files")
            if qf:
                files.extend(qf)
        log.info("inbox_drain", team_id=team_id, conv_key=conv_key, count=len(queued))
        await run_agent(
            client=client,
            team_id=team_id,
            slack_user_id=latest.get("slack_user_id"),
            channel=latest.get("channel") or channel,
            user_text=coalesced_text,
            user_ts=latest.get("user_ts"),
            conversation_key=conv_key,
            reply_thread_ts=latest.get("reply_thread_ts"),
            require_existing_thread=bool(latest.get("require_existing_thread")),
            files=files or None,
            lock_handle=handle,
        )
        handle = None  # ownership transferred to run_agent
    finally:
        if handle is not None:
            await handle.release()


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #

async def run_agent(*, client, team_id: str | None, slack_user_id: str | None, channel: str,
                    user_text: str, user_ts: str, conversation_key: str, reply_thread_ts: str | None,
                    require_existing_thread: bool = False,
                    files: list[dict] | None = None,
                    lock_handle: ThreadLockHandle | None = None) -> None:
    # The lock_handle is the per-thread mutex acquired in the Slack handler.
    # It MUST be released on every exit path: interrupt (so the next inbound
    # message can enter and resume), end_turn (so queued items get drained),
    # validation early-returns, and any exception. We pass it through ctx so
    # _drive can release at the precise point (after posting the gate, etc.).
    try:
        await _run_agent_impl(
            client=client, team_id=team_id, slack_user_id=slack_user_id,
            channel=channel, user_text=user_text, user_ts=user_ts,
            conversation_key=conversation_key, reply_thread_ts=reply_thread_ts,
            require_existing_thread=require_existing_thread, files=files,
            lock_handle=lock_handle,
        )
    finally:
        if lock_handle is not None:
            await lock_handle.release()


async def _run_agent_impl(*, client, team_id, slack_user_id, channel, user_text, user_ts,
                          conversation_key, reply_thread_ts, require_existing_thread,
                          files, lock_handle):
    text = user_text.strip()
    has_files = bool(files)
    if (not text and not has_files) or not team_id or not slack_user_id:
        return

    # Channel follow-ups: only continue a thread Sebitas already started.
    if require_existing_thread:
        async with get_session() as session:
            workspace = await repo.get_workspace(session, team_id)
            if workspace is None or await repo.get_thread(session, workspace.id, channel, conversation_key) is None:
                return

    await _add_reaction(client, channel, user_ts)
    settings = get_settings()

    # Process file attachments before the agent loop. Needs workspace_id first
    # (for the per-tenant R2 prefix); persistence of the user message + the
    # attachment rows happens after, in a single transaction.
    file_blocks: list[dict] = []
    text_prepend = ""
    attachments_records: list[dict] = []
    if has_files:
        workspace_id_pre = await _ensure_workspace(team_id)
        # Status icon + text: audio gets a dedicated note since transcription
        # adds a few seconds and the user should know what's happening.
        has_audio_like = any(
            (f.get("mimetype") or "").startswith(("audio/", "video/")) for f in files
        )
        if has_audio_like and len(files) == 1:
            status_text = ":microphone: Transcribiendo..."
        elif has_audio_like:
            status_text = f":microphone: Transcribiendo + procesando {len(files)} archivo(s)..."
        else:
            status_text = f":paperclip: Procesando {len(files)} archivo(s)..."
        try:
            await client.chat_postMessage(
                channel=channel, thread_ts=reply_thread_ts or user_ts,
                text=status_text,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("status_post_failed", error=str(exc))
        from app.slack import files as sf
        try:
            # Resolve the per-workspace bot token. If the workspace isn't
            # installed (shouldn't happen if we got an event from there), the
            # file download will 401 and we'll surface that as "unsupported".
            from app.slack.tokens import get_bot_token_by_workspace
            _ws_pair = await get_bot_token_by_workspace(workspace_id_pre)
            _bot_token = _ws_pair[0] if _ws_pair else ""
            result_files = await sf.process_files(str(workspace_id_pre), files, _bot_token)
        except Exception as exc:  # noqa: BLE001
            log.warning("process_files_failed", error=str(exc))
            result_files = {
                "blocks": [], "text_prepend": "", "attachments": [],
                "unsupported": [f"Error procesando archivos: {exc}"],
                "supported_count": 0, "types": [],
            }
        file_blocks = result_files["blocks"]
        text_prepend = result_files["text_prepend"]
        attachments_records = result_files["attachments"]
        if result_files["unsupported"]:
            try:
                await client.chat_postMessage(
                    channel=channel, thread_ts=reply_thread_ts or user_ts,
                    text=":warning: Algunos archivos no los procesé:\n• " + "\n• ".join(result_files["unsupported"]),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("unsupported_post_failed", error=str(exc))
        log.info(
            "file_ingest_done",
            n_files=len(files), supported=result_files["supported_count"],
            types=result_files["types"],
        )
        # Nothing usable and no user text: bail out gracefully (no agent run).
        if result_files["supported_count"] == 0 and not text:
            await _remove_reaction(client, channel, user_ts)
            return

    # YouTube link detection in the user text. Each unique video_id becomes a
    # logical attachment (no R2 file): we fetch captions + oEmbed metadata and
    # add a [YouTube transcript ...] header to text_prepend.
    yt_extra_text, yt_records, yt_unsupported = await _process_youtube_links(text, client, channel, reply_thread_ts or user_ts)
    if yt_extra_text:
        text_prepend = (text_prepend + "\n\n" + yt_extra_text) if text_prepend else yt_extra_text
    if yt_records:
        attachments_records.extend(yt_records)
    if yt_unsupported:
        try:
            await client.chat_postMessage(
                channel=channel, thread_ts=reply_thread_ts or user_ts,
                text=":warning: Algunos links de YouTube no los procesé:\n• " + "\n• ".join(yt_unsupported),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("yt_unsupported_post_failed", error=str(exc))

    full_text = text
    if text_prepend:
        full_text = (text_prepend + "\n\n" + text) if text else text_prepend

    history = await _load_history(team_id, channel, conversation_key)
    workspace_id, app_user_id = await _persist_user(
        team_id, channel, conversation_key, slack_user_id, full_text, user_ts,
        attachments=attachments_records,
    )

    # Build the seed user content: blocks first (so the model "reads" attachments
    # before the question), then text last. Plain string for the no-attachment path.
    if file_blocks:
        seed_user_content: Any = list(file_blocks) + [{"type": "text", "text": full_text or " "}]
    else:
        seed_user_content = full_text
    seed = history + [{"role": "user", "content": seed_user_content}]

    run_id = f"{conversation_key}:{user_ts}"
    # Per-user skills context (always_active bodies + on_demand descriptions,
    # built fresh each turn so install/uninstall takes effect on the next reply).
    skills_context = await build_skills_context(app_user_id)
    # Slack roster: ensure the workspace's user list is synced (lazy, cheap on
    # warm cache) then build a compact channel-member block for this run so the
    # agent can use real <@U...> mentions without an extra tool round-trip.
    try:
        await _roster.ensure_workspace_synced(workspace_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("roster_sync_failed", workspace_id=str(workspace_id), error=str(exc))
    channel_roster_text = await _build_channel_roster_block(workspace_id, channel)
    set_run_context(
        workspace_id=str(workspace_id), run_id=run_id,
        skills_context=skills_context, channel_roster=channel_roster_text,
        app_user_id=str(app_user_id),
    )
    ctx = {
        "run_id": run_id, "seed_len": len(seed), "team_id": team_id,
        "workspace_id": str(workspace_id),
        "app_user_id": str(app_user_id),
        "channel": channel, "conversation_key": conversation_key,
        "reply_thread_ts": reply_thread_ts, "user_ts": user_ts,
    }
    config = {"configurable": {"thread_id": run_id}}

    with _langfuse.start_as_current_observation(
        as_type="span", name="agent-run",
        input={"text": text, "n_files": len(files or [])},
    ), propagate_attributes(
        session_id=f"{team_id}:{channel}:{conversation_key}",
        user_id=slack_user_id, tags=["slack", "agent"], metadata={"tenant": team_id},
    ):
        result = await get_graph().ainvoke({"messages": seed, "iterations": 0}, config)
        await _drive(client, ctx, result, lock_handle=lock_handle)


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
    # The original ctx carries `app_user_id` so the skills context is rebuilt
    # for the same user who opened the run; if for some reason it's missing
    # (older ctx from before this slice), skills_context degrades to "" and
    # only the non-skill prompt is sent.
    async with get_session() as session:
        workspace = await repo.get_workspace(session, ctx["team_id"])
    if workspace is not None:
        app_user_str = ctx.get("app_user_id")
        skills_ctx = ""
        if app_user_str:
            try:
                skills_ctx = await build_skills_context(uuid.UUID(app_user_str))
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "skills_context_rebuild_failed",
                    error=str(exc), app_user_id=app_user_str,
                )
        set_run_context(
            workspace_id=str(workspace.id),
            run_id=ctx["run_id"],
            skills_context=skills_ctx,
            app_user_id=app_user_str,
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
    — build one from the workspace's bot token. Idempotent via resume_run's
    snapshot guard."""
    from slack_sdk.web.async_client import AsyncWebClient

    # Resolve per-workspace bot token from the ctx. Without it we can't post
    # back to that Slack workspace -- log + bail rather than crash.
    from app.slack.tokens import get_bot_token_by_workspace
    ws_pair = await get_bot_token_by_workspace(uuid.UUID(ctx["workspace_id"]))
    if not ws_pair:
        log.warning("resume_after_connect_no_token", workspace_id=ctx.get("workspace_id"))
        return
    client = AsyncWebClient(token=ws_pair[0])
    await resume_run(client=client, ctx=ctx, decision="connected")
