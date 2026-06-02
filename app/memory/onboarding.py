"""Workspace onboarding scan (slice T-X Phase D).

Triggered by the agent's `aprende_workspace` tool when the user says
"aprende del workspace" / "/misterr aprende". Walks four data sources
and writes observations to the `company` + `team` memory skills so the
agent has real context from day one instead of empty stubs.

Sources:

1. **Channels list** -- `conversations.list` (the bot's accessible channels).
   For each: name + purpose + member count. Written to `team`.
2. **Member profiles** -- read from the local SlackUser cache (already
   synced by `roster.ensure_workspace_synced`). For each non-bot user:
   display_name + real_name + title (when available). Written to `team`.
3. **Integrations** -- read from the local IntegrationConnection table.
   Listed as a single observation summarizing the stack. Written to `company`.
4. **Historical messages** -- top N channels by member count, last M
   messages each, fed in parallel to haiku for fact extraction. Extracted
   facts are written to `company`.

Sources are independent: a failure in one is logged and the others still
run. The function returns a summary dict the caller (the agent tool) can
report back to the user.

Bounded work:
  - `HISTORY_TOP_CHANNELS` (default 5) channels scanned for messages.
  - `HISTORY_MESSAGES_PER_CHANNEL` (default 20) messages per channel.
  - `HISTORY_FACTS_PER_CHANNEL` (default 5) facts extracted per channel.
  - haiku calls run concurrently with `asyncio.gather`.

Idempotency: re-running just re-scans and re-appends. Duplicates are
folded by Phase C compaction. We do NOT keep a "last scanned at" marker
in this slice; if the user re-runs in a week, they get fresh observations.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

import litellm
import structlog
from langfuse import get_client
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import select

from app.config import get_settings
from app.db.models import IntegrationConnection, SlackUser
from app.db.session import get_session
from app.memory import append, seed
from app.memory.constants import COMPANY_SLUG, TEAM_SLUG
from app.slack.tokens import get_bot_token_by_workspace

log = structlog.get_logger(__name__)
_langfuse = get_client()


# Bounds on the historical-messages scan. Wide enough to surface signal,
# tight enough to keep wall-clock + token cost predictable for a one-shot.
HISTORY_TOP_CHANNELS: int = 5
HISTORY_MESSAGES_PER_CHANNEL: int = 20
HISTORY_FACTS_PER_CHANNEL: int = 5

# Cap on how many members + channels we write as `team` observations.
# A workspace with 200 channels would otherwise spam 200 bullets and
# trigger compaction immediately; capping keeps the log digestible.
MAX_CHANNEL_OBSERVATIONS: int = 30
MAX_MEMBER_OBSERVATIONS: int = 50


_FACT_EXTRACTION_PROMPT = """\
Tarea: extraer hechos DURABLES y específicos de una muestra de mensajes
de un canal de Slack. Esto NO es una respuesta a un usuario; es un proceso
interno de aprendizaje del workspace.

Canal: #{channel_name}
Propósito declarado del canal: {channel_purpose}

MENSAJES (más recientes primero):

{messages}

INSTRUCCIONES:

1. Devolvé un JSON array de strings. Cada string es UN hecho durable
   sobre la empresa, el producto, el equipo, las herramientas, los
   procesos, o el mercado. Ejemplos buenos:
     "El producto se llama X y se usa para Y"
     "Sam reporta a Laura"
     "El stack de datos es Snowflake + dbt + Sigma"
2. Máximo {max_facts} hechos.
3. Saltate: bromas, chitchat, opiniones, eventos efímeros ("hoy hay daily"),
   referencias a tickets / PRs específicos sin contexto.
4. Cada hecho < 200 caracteres, declarativo, en español.
5. Si el canal solo tiene chitchat / no hay nada durable, devolvé [].

OUTPUT: solo el JSON array. Sin preámbulo, sin código fenced, sin
comentarios. Si no hay hechos, devolvé exactamente: []
"""


async def _scan_integrations(workspace_id: uuid.UUID) -> dict[str, int]:
    """Read IntegrationConnection rows + write a single observation
    summarizing the workspace's connected stack."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(IntegrationConnection.app, IntegrationConnection.provider)
                .where(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.status == "connected",
                )
            )
        ).all()
    if not rows:
        return {"integrations_written": 0}

    # Dedupe by app name; we don't care if there are multiple Linear connections.
    names = sorted({r.app for r in rows if r.app})
    if not names:
        return {"integrations_written": 0}

    text = f"Integraciones conectadas en el workspace: {', '.join(names)}."
    ok = await append.append_observation(
        workspace_id, COMPANY_SLUG, text=text, source="onboarding-scan"
    )
    return {"integrations_written": 1 if ok else 0}


async def _scan_members(workspace_id: uuid.UUID) -> dict[str, int]:
    """Read the SlackUser cache + write per-member observations to `team`.
    Roster is already kept fresh by the lifespan roster_periodic task and
    by `ensure_workspace_synced` lazy calls."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(
                    SlackUser.slack_user_id,
                    SlackUser.display_name,
                    SlackUser.real_name,
                    SlackUser.tz,
                ).where(
                    SlackUser.workspace_id == workspace_id,
                    SlackUser.is_bot.is_(False),
                    SlackUser.deleted.is_(False),
                )
            )
        ).all()

    written = 0
    for row in rows[:MAX_MEMBER_OBSERVATIONS]:
        label = row.display_name or row.real_name or row.slack_user_id
        parts = [f"<@{row.slack_user_id}> ({label})"]
        if row.real_name and row.real_name != label:
            parts.append(f"nombre: {row.real_name}")
        if row.tz:
            parts.append(f"tz: {row.tz}")
        ok = await append.append_observation(
            workspace_id, TEAM_SLUG, text=", ".join(parts), source="onboarding-scan"
        )
        if ok:
            written += 1
    return {"members_written": written}


async def _scan_channels(
    workspace_id: uuid.UUID, client: AsyncWebClient
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """List the workspace's public channels via the Slack API. Write up
    to MAX_CHANNEL_OBSERVATIONS to `team`. Returns the summary dict AND
    the channels list (sorted by num_members desc) so the historical-
    messages scan can pick the top N without a second API call."""
    channels: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        try:
            resp = await client.conversations_list(
                exclude_archived=True,
                types="public_channel",
                limit=200,
                cursor=cursor,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "onboarding_channels_list_failed",
                workspace_id=str(workspace_id),
                error=str(exc)[:200],
            )
            break
        for ch in resp.get("channels", []) or []:
            channels.append(ch)
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break

    channels.sort(key=lambda c: int(c.get("num_members") or 0), reverse=True)

    written = 0
    for ch in channels[:MAX_CHANNEL_OBSERVATIONS]:
        name = ch.get("name") or "(sin nombre)"
        purpose = (ch.get("purpose") or {}).get("value") or "sin propósito"
        topic = (ch.get("topic") or {}).get("value") or ""
        members = ch.get("num_members") or 0
        purpose_short = purpose.replace("\n", " ").strip()[:200]
        topic_short = topic.replace("\n", " ").strip()[:120]
        body = f"#{name} ({members} miembros): {purpose_short or 'sin propósito'}"
        if topic_short and topic_short != purpose_short:
            body += f" — topic: {topic_short}"
        ok = await append.append_observation(
            workspace_id, TEAM_SLUG, text=body, source="onboarding-scan"
        )
        if ok:
            written += 1
    return {"channels_written": written}, channels


async def _extract_facts_for_channel(
    *,
    channel_id: str,
    channel_name: str,
    channel_purpose: str,
    messages: list[dict[str, Any]],
) -> list[str]:
    """One haiku call. Returns the extracted facts as a list of strings.
    On parse failure / API error / empty input, returns []."""
    if not messages:
        return []
    # Newest first per Slack's response, which the prompt expects.
    formatted: list[str] = []
    for m in messages:
        user = m.get("user") or m.get("bot_id") or "unknown"
        text = (m.get("text") or "").replace("\n", " ").strip()
        if not text:
            continue
        formatted.append(f"<@{user}>: {text[:400]}")
    if not formatted:
        return []

    prompt = _FACT_EXTRACTION_PROMPT.format(
        channel_name=channel_name,
        channel_purpose=channel_purpose or "(sin propósito)",
        messages="\n".join(formatted),
        max_facts=HISTORY_FACTS_PER_CHANNEL,
    )

    settings = get_settings()
    model = settings.cheap_model
    try:
        with _langfuse.start_as_current_observation(
            as_type="generation",
            name="memory:onboarding:extract",
            model=model,
            input=prompt,
        ) as gen:
            response = await litellm.acompletion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800,
            )
            raw = (response.choices[0].message.content or "").strip()
            usage = getattr(response, "usage", None)
            if usage is not None:
                gen.update(
                    output=raw,
                    usage_details={
                        "input": getattr(usage, "prompt_tokens", 0) or 0,
                        "output": getattr(usage, "completion_tokens", 0) or 0,
                    },
                )
            else:
                gen.update(output=raw)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "onboarding_extract_failed",
            channel_id=channel_id,
            error=str(exc)[:200],
        )
        return []

    # Strip accidental code fences.
    raw = re.sub(r"^```(?:\w+)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw).strip()
    if not raw or raw == "[]":
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning(
            "onboarding_extract_unparseable",
            channel_id=channel_id,
            sample=raw[:200],
        )
        return []
    if not isinstance(parsed, list):
        return []
    facts: list[str] = []
    for item in parsed:
        if isinstance(item, str) and item.strip():
            facts.append(item.strip()[:480])
        if len(facts) >= HISTORY_FACTS_PER_CHANNEL:
            break
    return facts


async def _scan_historical_messages(
    workspace_id: uuid.UUID,
    client: AsyncWebClient,
    channels: list[dict[str, Any]],
) -> dict[str, int]:
    """For each of the top N channels (already sorted by member count in
    `_scan_channels`), fetch the last M messages and ask haiku to extract
    durable facts. Per-channel haiku calls run concurrently."""
    top = [c for c in channels[:HISTORY_TOP_CHANNELS] if c.get("id")]
    if not top:
        return {"facts_written": 0, "channels_scanned": 0}

    async def _fetch_then_extract(ch: dict[str, Any]) -> list[str]:
        channel_id = ch["id"]
        try:
            resp = await client.conversations_history(
                channel=channel_id,
                limit=HISTORY_MESSAGES_PER_CHANNEL,
            )
        except Exception as exc:  # noqa: BLE001
            # Most common: not_in_channel for channels Misterr isn't a member of.
            log.info(
                "onboarding_history_skip",
                channel_id=channel_id,
                channel_name=ch.get("name"),
                reason=str(exc)[:100],
            )
            return []
        msgs = resp.get("messages") or []
        return await _extract_facts_for_channel(
            channel_id=channel_id,
            channel_name=ch.get("name") or channel_id,
            channel_purpose=(ch.get("purpose") or {}).get("value") or "",
            messages=msgs,
        )

    results = await asyncio.gather(
        *[_fetch_then_extract(ch) for ch in top], return_exceptions=True
    )

    written = 0
    scanned = 0
    for ch, facts in zip(top, results):
        if isinstance(facts, BaseException):
            log.warning(
                "onboarding_history_task_failed",
                channel_id=ch.get("id"),
                error=str(facts)[:200],
            )
            continue
        scanned += 1
        for fact in facts:
            prefixed = f"[#{ch.get('name')}] {fact}"
            ok = await append.append_observation(
                workspace_id,
                COMPANY_SLUG,
                text=prefixed,
                source="onboarding-scan",
            )
            if ok:
                written += 1
    return {"facts_written": written, "channels_scanned": scanned}


async def run_onboarding_scan(workspace_id: uuid.UUID) -> dict[str, Any]:
    """Top-level entry. Walks all four sources. Per-source errors are
    logged and skipped; the function always returns a summary dict.

    The caller (`aprende_workspace` agent tool) is expected to format the
    summary back to the user.
    """
    # Make sure the workspace's memory stubs exist before we start appending.
    # They normally do (seeded on install), but if a workspace pre-dates the
    # memory slice, this catches it.
    await seed.ensure_company_skill(workspace_id)
    await seed.ensure_team_skill(workspace_id)

    summary: dict[str, Any] = {}

    # Integrations + members are local DB reads; they don't need the Slack
    # client. Run them in parallel with the channels-list API call.
    pair = await get_bot_token_by_workspace(workspace_id)
    client: AsyncWebClient | None = None
    if pair:
        client = AsyncWebClient(token=pair[0])

    integ_task = asyncio.create_task(_scan_integrations(workspace_id))
    members_task = asyncio.create_task(_scan_members(workspace_id))

    channels: list[dict[str, Any]] = []
    if client is not None:
        try:
            channels_summary, channels = await _scan_channels(workspace_id, client)
            summary.update(channels_summary)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "onboarding_channels_scan_unexpected",
                workspace_id=str(workspace_id),
                error=str(exc)[:200],
            )
            summary["channels_written"] = 0
    else:
        log.warning(
            "onboarding_no_bot_token",
            workspace_id=str(workspace_id),
        )
        summary["channels_written"] = 0

    summary.update(await integ_task)
    summary.update(await members_task)

    if client is not None and channels:
        try:
            summary.update(
                await _scan_historical_messages(workspace_id, client, channels)
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "onboarding_history_scan_unexpected",
                workspace_id=str(workspace_id),
                error=str(exc)[:200],
            )
            summary["facts_written"] = 0
            summary["channels_scanned"] = 0
    else:
        summary["facts_written"] = 0
        summary["channels_scanned"] = 0

    log.info(
        "onboarding_scan_complete",
        workspace_id=str(workspace_id),
        **summary,
    )
    return summary


__all__ = ["run_onboarding_scan"]
