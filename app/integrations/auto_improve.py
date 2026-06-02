"""Auto-improve for integration skills.

After every agent turn that included one or more `run_action` calls,
this module runs a cheap-model post-pass that looks at:

  - what the user wanted (`user_text`)
  - what the agent replied (`agent_response`)
  - which (app, action_id) the agent invoked

…and asks: "Is there a generalizable lesson here that would help the
agent pick a better action next time?" If yes, the lesson is appended
to the `## Usage notes` section of the `integrations/<app>` skill
(preserved across the daily Pipedream-catalog refresh).

Guard rails so we don't poison skills with noise:

  - **Prompt biased to NULL.** The haiku prompt explicitly says "default
    to no insight if uncertain". One observation per app per turn max.
  - **Append-only.** We never edit or delete existing notes.
  - **Hard cap.** Up to `MAX_USAGE_NOTES` (20) per skill. Beyond that the
    oldest dated entries are dropped (silently — admins can re-emit).
  - **Same-line dedup.** If a new insight matches verbatim an existing
    one, we drop it instead of appending.

Failure modes are intentionally silent: any exception short-circuits the
post-pass for this turn. The agent's user-facing reply is already posted
by the time we run.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone

import litellm
import structlog
from langfuse import get_client
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Skill
from app.db.session import get_session
from app.integrations.catalog_skills import (
    MAX_BODY_BYTES,
    SECTION_USAGE,
    _slug_for,
)
from app.skills import registry as skill_registry
from app.skills import storage as skill_storage

log = structlog.get_logger(__name__)
_langfuse = get_client()


# Hard limit on insights per skill before we start dropping old ones.
MAX_USAGE_NOTES: int = 20

# Max length of a single insight, post-trim. The haiku prompt asks for
# 1-2 sentences but the model occasionally exceeds; we truncate rather
# than reject to avoid losing useful signal.
MAX_INSIGHT_CHARS: int = 320


_INSIGHT_PROMPT = """\
Task: review ONE turn between a user and an AI assistant in Slack in
which the assistant used the integration `{app}` via one or more
actions. Your job is to detect a SPECIFIC PARAM error or ACTION
choice error and emit guidance to fix it in future turns.

The insight will be embedded in the agent's system prompt at decision
time. It MUST be in English (the rest of the agent's prompt + tool
descriptions are English; mixing languages degrades retrieval).

CONTEXT:

<user_request>
{user_text}
</user_request>

<assistant_response>
{agent_response}
</assistant_response>

<actions_invoked>
{actions_detailed}
</actions_invoked>

INSTRUCTIONS:

1. Return a JSON object with TWO fields:
     - "has_insight": boolean
     - "insight": string in English, ≤ {max_chars} chars

2. Look for ONE of these specific failure modes. If none fit, return
   `has_insight: false`:

   **A) PARAM ERROR** (most important): the assistant picked the right
   action but set a CRITICAL parameter wrong. Examples:
     - boolean flag `include*` set to `false` when the user's intent
       required the included data (e.g. `includeParties=false` when
       the user was filtering by company name)
     - date filter too narrow when the user did not constrain it
     - `maxResults` too low, losing data
     - required parameter omitted -> action returned empty data
   The insight must name the SPECIFIC PARAM and the CORRECT VALUE:
   "When the user asks to filter calls by company/account, set
   `includeParties=true` on gong-get-extensive-data and filter
   client-side on parties[].name."

   **B) WRONG ACTION**: the assistant chose a weaker action when a
   better one existed in the catalog. The insight must name BOTH
   actions and explain when to switch:
   "When the intent is filtering by name, do NOT use list-X (returns
   only ids), use get-extensive-X with the right filters."

3. EMIT NULL (`has_insight: false`) when:
     - The turn was a direct success (no error to correct).
     - The failure was on the API side, not the assistant.
     - The assistant responded asking for clarification (that's OK,
       it's not an integration-usage error -- do NOT say "ask for
       filters first" because that postpones the bug, doesn't solve it).
     - The failure cause is ambiguous and you can't point to a
       specific param or a specific action.
     - When you're unsure. Precision matters more than recall.

4. FORBIDDEN:
     - Notes like "ask user for more context" or "ask for filters
       before invoking" (not insights, they push the problem back).
     - Generic notes ("optimize the search", "reduce cost").
     - Notes without clear evidence in the turn.
     - Notes specific to one user / workspace / date.

5. Do NOT invent recommendations not backed by evidence in the turn.
   If the only evidence is "the agent failed" but you can't say WHY
   or WHAT it should have done, return `has_insight: false`.

OUTPUT: only the JSON object. No preamble, no code fences, no
comments. When `has_insight: false`, omit the `insight` field or set
it to "".
"""


def _trim(text: str, cap: int = 4000) -> str:
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    return text[:cap].rstrip() + "…"


async def _extract_insight(
    user_text: str,
    agent_response: str,
    app: str,
    action_calls: list[dict],
) -> str | None:
    """One haiku call. Returns the insight string when emitted, else
    None. Never raises.

    `action_calls` is a list of `{action_id, params}` dicts so the
    haiku can see PARAM-level decisions (not just which action was
    chosen). This is what unlocks PARAM-error insights like
    `includeParties=false` being wrong for company-filter intents."""
    if not action_calls:
        return None
    detailed_lines: list[str] = []
    for c in action_calls[:10]:
        aid = c.get("action_id") or "?"
        params = c.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        # Format each param compactly. Booleans + small primitives
        # are most informative; trim long values to keep the prompt
        # focused.
        if params:
            param_bits = []
            for k, v in list(params.items())[:15]:
                if isinstance(v, (str, int, float, bool)):
                    val = str(v)[:60]
                else:
                    val = "[…]"  # collapse objects/lists for brevity
                param_bits.append(f"{k}={val}")
            params_repr = ", ".join(param_bits)
        else:
            params_repr = "(no params passed)"
        detailed_lines.append(f"- `{aid}` called with: {params_repr}")
    actions_detailed = "\n".join(detailed_lines)

    prompt = _INSIGHT_PROMPT.format(
        app=app,
        user_text=_trim(user_text) or "(vacío)",
        agent_response=_trim(agent_response) or "(vacío)",
        actions_detailed=actions_detailed,
        max_chars=MAX_INSIGHT_CHARS,
    )
    settings = get_settings()
    model = settings.cheap_model
    try:
        with _langfuse.start_as_current_observation(
            as_type="generation",
            name="integration:auto-improve",
            model=model,
            input=prompt,
        ) as gen:
            response = await litellm.acompletion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
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
        log.info("auto_improve_model_failed", app=app, error=str(exc)[:200])
        return None

    raw = re.sub(r"^```(?:\w+)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw).strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.info("auto_improve_unparseable", app=app, sample=raw[:200])
        return None
    if not isinstance(parsed, dict):
        return None
    if not parsed.get("has_insight"):
        return None
    insight = parsed.get("insight")
    if not isinstance(insight, str) or not insight.strip():
        return None
    insight = insight.strip().replace("\n", " ")
    if len(insight) > MAX_INSIGHT_CHARS:
        insight = insight[:MAX_INSIGHT_CHARS].rstrip() + "…"
    return insight


def _bullets_in_usage_section(body: str) -> tuple[int, int, list[str]]:
    """Locate the `## Usage notes` section. Returns
    (section_start_idx, section_end_idx, existing_bullets)."""
    idx = body.find(SECTION_USAGE)
    if idx == -1:
        return -1, -1, []
    section_after_header = body[idx + len(SECTION_USAGE):]
    # Section ends at the next H2 (or EOF).
    end_match = re.search(r"\n## ", section_after_header)
    end_idx = (
        idx + len(SECTION_USAGE) + end_match.start()
        if end_match
        else len(body)
    )
    section_text = body[idx + len(SECTION_USAGE):end_idx]
    bullets = [
        line.strip()
        for line in section_text.splitlines()
        if line.strip().startswith("- ")
    ]
    return idx, end_idx, bullets


def _build_usage_note_line(insight: str, when: datetime) -> str:
    ts = when.strftime("%Y-%m-%d")
    return f"- {ts} [auto-improve]: {insight}"


async def _append_usage_note(workspace_id: uuid.UUID, app: str, insight: str) -> bool:
    """Append a single bullet to the `## Usage notes` section of
    `integrations/<app>`. Cap at MAX_USAGE_NOTES. Returns True on a
    successful write; False on any silent failure (skill missing,
    dedup hit, body too large, optimistic-lock conflict)."""
    slug = _slug_for(app)
    async with get_session() as session:
        skill = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace_id,
                    Skill.name == slug,
                )
            )
        ).scalar_one_or_none()
    if skill is None:
        log.info(
            "auto_improve_skill_missing",
            workspace_id=str(workspace_id), app=app,
        )
        return False

    try:
        body = await skill_storage.download_skill_body(
            workspace_id=skill.workspace_id,
            skill_id=skill.id,
            version=skill.version,
            r2_ref=skill.body_r2_ref,
        )
    except Exception as exc:  # noqa: BLE001
        log.info(
            "auto_improve_body_read_failed",
            app=app, error=str(exc)[:200],
        )
        return False

    start_idx, end_idx, existing = _bullets_in_usage_section(body)
    if start_idx == -1:
        # No Usage notes section (corrupted skill). Don't try to
        # recreate the structure here -- let the next catalog refresh
        # rebuild the skill from scratch.
        log.info("auto_improve_section_missing", app=app)
        return False

    new_line = _build_usage_note_line(insight, datetime.now(timezone.utc))

    # Dedup: same exact text already present.
    for line in existing:
        if line.split("]: ", 1)[-1].strip().lower() == insight.strip().lower():
            return False

    # Cap: keep only the latest (MAX_USAGE_NOTES - 1) so we have room
    # for the new one. We drop OLDEST since newer insights reflect
    # more recent learnings.
    kept = existing[-(MAX_USAGE_NOTES - 1):]
    new_section = SECTION_USAGE + "\n\n" + "\n".join(kept + [new_line]) + "\n"
    new_body = body[:start_idx] + new_section + body[end_idx:]

    new_size = len(new_body.encode("utf-8"))
    if new_size > MAX_BODY_BYTES:
        log.warning(
            "auto_improve_body_cap_hit", app=app, size=new_size,
        )
        return False

    try:
        await skill_registry.update_skill_body(
            skill_id=skill.id, new_body=new_body, new_size_bytes=new_size
        )
    except Exception as exc:  # noqa: BLE001
        log.info(
            "auto_improve_write_failed",
            app=app, error=str(exc)[:200],
        )
        return False

    log.info(
        "auto_improve_appended",
        workspace_id=str(workspace_id),
        app=app,
        note=insight[:100],
    )
    return True


async def maybe_improve_skill(
    *,
    workspace_id: uuid.UUID,
    user_text: str,
    agent_response: str,
    integration_calls: list[dict],
) -> int:
    """Entry point from the runner's post-completion path. Best-effort;
    returns the number of insights appended (often 0)."""
    if not integration_calls:
        return 0
    # Group calls (action_id + params) by app so we make ONE haiku
    # call per app touched in this turn. Passing params lets haiku see
    # param-level mistakes like `includeParties=false` when filtering
    # by company.
    by_app: dict[str, list[dict]] = {}
    for call in integration_calls:
        app = (call.get("app") or "").lower().strip()
        if not app:
            continue
        aid = call.get("action_id") or call.get("component_id")
        if not isinstance(aid, str) or not aid:
            continue
        by_app.setdefault(app, []).append({
            "action_id": aid,
            "params": call.get("params") or {},
        })

    written = 0
    for app, action_calls in by_app.items():
        try:
            insight = await _extract_insight(
                user_text, agent_response, app, action_calls
            )
        except Exception as exc:  # noqa: BLE001
            log.info("auto_improve_unexpected", app=app, error=str(exc)[:200])
            continue
        if not insight:
            continue
        if await _append_usage_note(workspace_id, app, insight):
            written += 1
        # Yield between apps.
        await asyncio.sleep(0)
    return written


__all__ = [
    "maybe_improve_skill",
    "MAX_USAGE_NOTES",
    "MAX_INSIGHT_CHARS",
]
