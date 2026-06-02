"""Auto-generated integration skills (slice T-X).

Goal: for every (workspace, connected_app) the agent has a skill called
`integrations/<app>` whose body lists the FULL Pipedream action catalog
for that app -- action key, description, key configurable props. The
agent loads this skill via `load_skill` before calling `run_action`, so
it never has to guess at component ids from training-data memory.

Two-section body:

  ## Available actions
  (auto-generated. Sweeper rewrites this verbatim from Pipedream.
  Existing content here is DISCARDED on refresh -- it's pure mirror.)

  ## Usage notes
  (preserved across refreshes. Admins edit by hand; the auto-improve
  post-pass appends learnings. Survives every sweep.)

Cache: the Pipedream catalog is the SAME across workspaces. We fetch
once per app (24h TTL in `_catalog_cache`) and reuse for every workspace
that has that app connected. 10 apps connected by 4 workspaces = 10
Pipedream queries per day, not 40.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any

import litellm
import structlog
from langfuse import get_client
from sqlalchemy import select

from app.config import get_settings
from app.db.models import IntegrationConnection, Skill, Workspace
from app.db.session import get_session
from app.integrations import pipedream as pd
from app.skills import registry as skill_registry

log = structlog.get_logger(__name__)
_langfuse = get_client()


# Cache: {app_slug: (rendered_body, cached_at_unix)}. TTL keeps Pipedream
# pressure low when the daily sweep runs against many workspaces at once.
_CATALOG_TTL_S: int = 24 * 60 * 60
_catalog_cache: dict[str, tuple[str, float]] = {}
_catalog_lock = asyncio.Lock()

# Markdown section headers. Must be exact strings -- the sweeper looks
# them up to extract + preserve the Usage notes section.
SECTION_AVAILABLE = "## Available actions"
SECTION_USAGE = "## Usage notes"

# Cap on body bytes. Same family of caps as memory skills; integrations
# with very large catalogs (Salesforce: ~80 actions) may approach this.
MAX_BODY_BYTES: int = 180_000

# Cap on how many props per action we render. Most apps' actions have
# 3-15 props; truncating beyond protects the prompt budget. The agent
# still has `get_component(action_id)` for the rare case it needs more.
MAX_PROPS_PER_ACTION: int = 12


def _fmt_prop(prop: dict[str, Any]) -> str:
    """One-line markdown bullet describing a configurable prop."""
    name = prop.get("name") or "?"
    type_ = prop.get("type") or "?"
    optional = " optional" if prop.get("optional") else ""
    desc = (prop.get("description") or "").replace("\n", " ").strip()
    if len(desc) > 120:
        desc = desc[:120].rstrip() + "…"
    if desc:
        return f"    - `{name}` ({type_}{optional}) — {desc}"
    return f"    - `{name}` ({type_}{optional})"


# Cache for haiku-generated usage hints per action. Hints don't change
# unless Pipedream changes the action's props -- safe to cache for the
# same 24h as the catalog itself. Key: (app_slug, action_key).
_action_hint_cache: dict[tuple[str, str], tuple[str, float]] = {}


_USAGE_HINT_PROMPT = """\
Task: read the definition of ONE action from an integration provider
and write OPERATIVE guidance (1-3 short sentences) so an AI agent
knows WHEN to use this action and HOW to set the key parameters.

The hint will be embedded in the agent's system prompt at decision
time. It MUST be in English (the rest of the agent's prompt is
English; mixing languages degrades retrieval).

ACTION:
  key:         {key}
  name:        {name}
  description: {description}

CONFIGURABLE PROPS:
{props_blob}

INSTRUCTIONS:

1. Return a JSON object with ONE field:
     - "hint": string in English, 1-3 short sentences, ≤ 400 chars.

2. The hint must answer TWO questions:
     a. When is this action the right pick? ("Use this when the user
        wants to ...")
     b. Which boolean / required param is CRITICAL to set correctly?
        Especially `include*` flags that change the response shape.

3. Be SPECIFIC. Bad: "use to fetch data". Good: "use to list calls;
   if you need company/contact info per call, set includeParties=true".

4. Do NOT invent capabilities that aren't in the props. Read the
   props and describe what they actually do.

5. If you can't conclude anything useful (generic action, obvious
   props), return `"hint": ""`.

OUTPUT: only the JSON object. No preamble, no code fences, no
comments. When there's no useful hint, return `{{"hint": ""}}`.
"""


def _props_blob(props: list[dict]) -> str:
    """Compact representation of props for the haiku prompt."""
    lines: list[str] = []
    for p in props[:20]:
        name = p.get("name", "?")
        type_ = p.get("type", "?")
        optional = " optional" if p.get("optional") else " required"
        desc = (p.get("description") or "").replace("\n", " ").strip()[:160]
        lines.append(f"  - {name} ({type_}{optional}): {desc}")
    return "\n".join(lines) if lines else "  (none)"


async def _generate_action_usage_hint(action: dict[str, Any]) -> str:
    """Single cheap-model call. Returns a 1-3 sentence operative guide
    for the agent on WHEN to use this action and WHAT params matter.
    Returns "" on any failure or when haiku decided there's nothing
    useful to say. Cached at module level by (app_implied_in_key, key)
    so the same action across workspaces only gets one haiku call per
    24h refresh."""
    key = action.get("key") or ""
    if not key:
        return ""
    cached = _action_hint_cache.get((key,))
    now = time.monotonic()
    if cached and (now - cached[1]) < _CATALOG_TTL_S:
        return cached[0]

    settings = get_settings()
    model = settings.cheap_model
    prompt = _USAGE_HINT_PROMPT.format(
        key=key,
        name=action.get("name") or key,
        description=(action.get("description") or "")[:600],
        props_blob=_props_blob(action.get("props") or []),
    )
    try:
        with _langfuse.start_as_current_observation(
            as_type="generation",
            name="catalog_skills:usage_hint",
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
        log.info("catalog_skill_hint_model_failed", key=key, error=str(exc)[:200])
        _action_hint_cache[(key,)] = ("", now)
        return ""

    raw = re.sub(r"^```(?:\w+)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw).strip()
    if not raw or raw == "{}":
        _action_hint_cache[(key,)] = ("", now)
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        _action_hint_cache[(key,)] = ("", now)
        return ""
    if not isinstance(parsed, dict):
        _action_hint_cache[(key,)] = ("", now)
        return ""
    hint = parsed.get("hint")
    if not isinstance(hint, str) or not hint.strip():
        _action_hint_cache[(key,)] = ("", now)
        return ""
    hint = hint.strip().replace("\n", " ")
    if len(hint) > 500:
        hint = hint[:500].rstrip() + "…"
    _action_hint_cache[(key,)] = (hint, now)
    return hint


async def _fetch_app_catalog(app: str) -> list[dict[str, Any]]:
    """Pull every action for `app` from Pipedream + enrich each with
    its configurable props. Returns a normalized list ready for
    rendering. Caches the FULL ENRICHED list (not just the action list)
    so subsequent renders avoid re-fetching props per app."""
    actions = await pd.search_actions(app, None)
    enriched: list[dict[str, Any]] = []
    for a in actions:
        key = a.get("key") or a.get("id")
        if not key:
            continue
        name = a.get("name") or a.get("title") or key
        desc = (a.get("description") or "").strip()
        props: list[dict] = []
        try:
            comp = await pd.get_component(key)
            props = comp.get("configurable_props") or []
        except Exception as exc:  # noqa: BLE001
            log.info(
                "catalog_skill_props_fetch_failed",
                app=app, action=key, error=str(exc)[:200],
            )
        # Strip auth ('app') props -- always present, never useful
        # for the agent to know about.
        props = [p for p in props if p.get("type") != "app"]
        action_dict = {
            "key": key,
            "name": name,
            "description": desc,
            "props": props,
        }
        # Ask haiku to produce 1-3 sentences of operative guidance:
        # "when to use this action" + "what flags matter". This is
        # what distinguishes "describe an API" from "tell an agent
        # how to use an API". Hints are cached per action key for
        # 24h, so the daily sweep hits haiku once per action across
        # the whole platform.
        action_dict["usage_hint"] = await _generate_action_usage_hint(action_dict)
        enriched.append(action_dict)
    return enriched


async def _get_or_build_available_section(app: str) -> str:
    """Return the rendered `## Available actions` markdown body for
    `app`. Cached for 24h; concurrent callers during a cold cache
    coalesce on the lock so we don't double-fetch."""
    now = time.monotonic()
    cached = _catalog_cache.get(app)
    if cached and (now - cached[1]) < _CATALOG_TTL_S:
        return cached[0]

    async with _catalog_lock:
        now = time.monotonic()
        cached = _catalog_cache.get(app)
        if cached and (now - cached[1]) < _CATALOG_TTL_S:
            return cached[0]

        try:
            actions = await _fetch_app_catalog(app)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "catalog_skill_fetch_failed", app=app, error=str(exc)[:200]
            )
            return ""

        lines: list[str] = [SECTION_AVAILABLE]
        if not actions:
            lines.append("")
            lines.append(
                f"(No Pipedream actions surfaced for `{app}`. The app may "
                "be supported via direct credentials only, or Pipedream's "
                "catalog hasn't indexed it yet.)"
            )
            body = "\n".join(lines)
            _catalog_cache[app] = (body, now)
            return body

        for a in actions:
            key = a["key"]
            lines.append("")
            lines.append(f"### `{key}`")
            if a.get("name") and a["name"] != key:
                lines.append(f"**{a['name']}**")
            if a.get("description"):
                lines.append(a["description"])
            # Operative guidance from haiku: when to use this action +
            # what params matter. The whole point of catalog skills is
            # to surface this kind of decision-relevant info; without
            # it the agent reads "Whether to include parties in the
            # response" and defaults to False because the description
            # is passive.
            if a.get("usage_hint"):
                lines.append("")
                lines.append(f"**When to use / key params:** {a['usage_hint']}")
            props = a.get("props") or []
            if props:
                lines.append("")
                lines.append("Configurable props:")
                for p in props[:MAX_PROPS_PER_ACTION]:
                    lines.append(_fmt_prop(p))
                if len(props) > MAX_PROPS_PER_ACTION:
                    lines.append(
                        f"    - … and {len(props) - MAX_PROPS_PER_ACTION} more "
                        f"(use `get_component('{key}')` for the full list)"
                    )

        body = "\n".join(lines)
        _catalog_cache[app] = (body, now)
        return body


def _extract_usage_notes(body: str | None) -> str:
    """Pull the `## Usage notes` section from an existing skill body so
    a refresh can put it back. Returns empty string if no such section
    (fresh skill, or admin deleted it)."""
    if not body:
        return ""
    idx = body.find(SECTION_USAGE)
    if idx == -1:
        return ""
    section = body[idx:]
    # Section ends at next H2 header at start of line, or EOF.
    end_match = re.search(r"\n## ", section[len(SECTION_USAGE):])
    if end_match:
        section = section[: len(SECTION_USAGE) + end_match.start()]
    return section.rstrip() + "\n"


async def _existing_body(skill: Skill) -> str | None:
    """Read the current body of an existing skill from R2. Returns None
    on any failure -- caller treats as "no prior body, regenerate from
    scratch with empty Usage notes"."""
    try:
        from app.skills import storage as skill_storage

        return await skill_storage.download_skill_body(
            workspace_id=skill.workspace_id,
            skill_id=skill.id,
            version=skill.version,
            r2_ref=skill.body_r2_ref,
        )
    except Exception as exc:  # noqa: BLE001
        log.info(
            "catalog_skill_body_read_failed",
            skill_id=str(skill.id), error=str(exc)[:200],
        )
        return None


def _slug_for(app: str) -> str:
    """Canonical skill name. Lowercased + slashed under `integrations/`."""
    return f"integrations/{app.lower().strip()}"


def _render_full_body(*, app: str, available: str, usage_notes: str) -> str:
    """Stitch the two sections together. Usage notes section is always
    present (header + body) so admins can find + edit it; an empty
    Usage notes section just has the header with a placeholder."""
    head = f"<!-- auto-generated integration skill for `{app}` -->\n"
    head += f"<!-- 'Available actions' is regenerated daily from Pipedream. -->\n"
    head += f"<!-- 'Usage notes' is preserved across refreshes (admin + auto-improve). -->\n\n"
    if not usage_notes.strip():
        usage_notes = (
            f"{SECTION_USAGE}\n"
            "(Sin notas todavía. Admins pueden editar a mano; el "
            "auto-improve post-pass agrega observaciones aprendidas "
            "del uso real.)\n"
        )
    return head + available + "\n\n" + usage_notes


async def upsert_integration_skill(
    workspace_id: uuid.UUID, app: str
) -> uuid.UUID | None:
    """Generate / refresh the `integrations/<app>` skill for a given
    workspace. Returns the skill id on success, None on failure.

    Preserves the existing `## Usage notes` section across refreshes
    -- only the `## Available actions` section is mirrored from
    Pipedream. New skills get a placeholder Usage notes.

    Best-effort: any failure (catalog fetch, R2 read, body write) logs
    and returns None without raising."""
    app = (app or "").lower().strip()
    if not app:
        return None

    slug = _slug_for(app)
    available = await _get_or_build_available_section(app)
    if not available:
        return None

    async with get_session() as session:
        existing = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace_id,
                    Skill.name == slug,
                )
            )
        ).scalar_one_or_none()

    usage_notes = ""
    if existing is not None:
        prior_body = await _existing_body(existing)
        usage_notes = _extract_usage_notes(prior_body)

    new_body = _render_full_body(
        app=app, available=available, usage_notes=usage_notes
    )
    new_size = len(new_body.encode("utf-8"))
    if new_size > MAX_BODY_BYTES:
        log.warning(
            "catalog_skill_body_too_large",
            app=app, size=new_size, cap=MAX_BODY_BYTES,
        )
        return None

    try:
        if existing is None:
            row = await skill_registry.create_skill(
                workspace_id=workspace_id,
                name=slug,
                description=(
                    f"Auto-generated catalog of Pipedream actions for `{app}`. "
                    "Load before calling `run_action` to see all available "
                    "actions + their configurable props."
                ),
                activation_default="on_demand",
                body=new_body,
                links=[],
                size_bytes=new_size,
                created_by_user_id=None,
                source="catalog",
                scope="workspace",
            )
            log.info(
                "catalog_skill_created",
                workspace_id=str(workspace_id), app=app, skill_id=str(row.id),
            )
            return row.id
        else:
            await skill_registry.update_skill_body(
                skill_id=existing.id, new_body=new_body, new_size_bytes=new_size
            )
            log.info(
                "catalog_skill_refreshed",
                workspace_id=str(workspace_id), app=app, skill_id=str(existing.id),
            )
            return existing.id
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "catalog_skill_upsert_failed",
            workspace_id=str(workspace_id), app=app, error=str(exc)[:200],
        )
        return None


async def delete_integration_skill(workspace_id: uuid.UUID, app: str) -> bool:
    """Remove the `integrations/<app>` skill from a workspace. Used
    when the connection is disconnected. Best-effort: returns True iff
    we deleted; False if nothing to delete or write failed."""
    slug = _slug_for(app)
    async with get_session() as session:
        existing = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace_id,
                    Skill.name == slug,
                )
            )
        ).scalar_one_or_none()
    if existing is None:
        return False
    try:
        await skill_registry.delete_skill(skill_id=existing.id)
        log.info(
            "catalog_skill_deleted",
            workspace_id=str(workspace_id), app=app,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "catalog_skill_delete_failed",
            workspace_id=str(workspace_id), app=app, error=str(exc)[:200],
        )
        return False


async def refresh_all_for_workspace(workspace_id: uuid.UUID) -> dict[str, int]:
    """Upsert a catalog skill for every connected integration in this
    workspace. Returns a counts dict for logging. Used by the daily
    sweeper + by the install / backfill paths."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(IntegrationConnection.app).where(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.status == "connected",
                    IntegrationConnection.provider == "pipedream",
                )
            )
        ).scalars().all()
    apps = sorted({(a or "").lower().strip() for a in rows if a})
    counts = {"apps": len(apps), "upserted": 0, "failed": 0}
    for app in apps:
        try:
            new_id = await upsert_integration_skill(workspace_id, app)
            if new_id:
                counts["upserted"] += 1
            else:
                counts["failed"] += 1
        except Exception as exc:  # noqa: BLE001
            counts["failed"] += 1
            log.warning(
                "catalog_skill_refresh_workspace_app_failed",
                workspace_id=str(workspace_id), app=app, error=str(exc)[:200],
            )
        # Yield between apps so we don't monopolize the loop.
        await asyncio.sleep(0)
    return counts


async def refresh_all_workspaces() -> dict[str, int]:
    """Daily-sweep entrypoint. Walks every installed workspace +
    refreshes skills for each of its connected integrations."""
    async with get_session() as session:
        ws_ids = (
            await session.execute(
                select(Workspace.id).where(Workspace.bot_token.isnot(None))
            )
        ).scalars().all()
    totals = {"workspaces": 0, "apps": 0, "upserted": 0, "failed": 0}
    for wid in ws_ids:
        try:
            counts = await refresh_all_for_workspace(wid)
            totals["workspaces"] += 1
            totals["apps"] += counts["apps"]
            totals["upserted"] += counts["upserted"]
            totals["failed"] += counts["failed"]
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "catalog_skill_refresh_workspace_failed",
                workspace_id=str(wid), error=str(exc)[:200],
            )
    return totals


# Background sweep loop, mounted from app.main lifespan.
SWEEP_INTERVAL_SECONDS: int = 24 * 60 * 60


async def run_catalog_sweep_loop(
    interval_seconds: int = SWEEP_INTERVAL_SECONDS,
) -> None:
    """Lifespan task. Sleeps `interval_seconds` between full sweeps.
    First sweep fires immediately after process start so any new
    actions Pipedream added since the last deploy get picked up
    without waiting a full day."""
    log.info("catalog_sweep_loop_started", interval_seconds=interval_seconds)
    try:
        while True:
            try:
                totals = await refresh_all_workspaces()
                if totals.get("upserted") or totals.get("failed"):
                    log.info("catalog_sweep_done", **totals)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("catalog_sweep_tick_failed", error=str(exc)[:200])
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        log.info("catalog_sweep_loop_cancelled")
        raise


__all__ = [
    "upsert_integration_skill",
    "delete_integration_skill",
    "refresh_all_for_workspace",
    "refresh_all_workspaces",
    "run_catalog_sweep_loop",
    "SECTION_AVAILABLE",
    "SECTION_USAGE",
]
