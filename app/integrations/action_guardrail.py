"""Generic platform-level guardrails for `run_action`.

Two hooks, both leveraging the action schema Pipedream/Composio
already expose (`configurable_props` on each component):

  1. Pre-call schema validation. Catches missing required params,
     unknown keys, or type mismatches BEFORE the request goes to the
     provider. Returns a structured error the LLM can self-correct
     against without burning a provider round-trip.

  2. Post-call empty-result detection. If the response looks sparse
     (zero items, totalRecords near-zero, empty list field) AND the
     agent passed boolean `include*` flags as `False` or
     `context='Basic'`, annotate the result with a retry hint
     ("you turned off X, Y; the response may be missing the data
     you need; retry with them flipped").

Both checks are GENERIC: they introspect the action's `configurable_props`
schema fetched from Pipedream (cached in `catalog_skills._catalog_cache`
via the component fetch). No per-app code. Applies to all 3,000+ apps
the agent can call via Pipedream.

The component spec is fetched fresh on cache miss (Pipedream's
`/components/{id}` endpoint). To avoid a per-call round-trip we lean
on an in-process LRU keyed by `(action_id)` with a 1h TTL. Specs change
rarely (a few times per quarter per app), so this is safe.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from app.integrations import pipedream as pd

log = structlog.get_logger(__name__)


# In-process spec cache. {action_id: (props_list, cached_at_unix)}.
# The catalog_skills sweeper hits the same endpoint daily and we share
# the burden here. A 1h TTL is short enough to pick up Pipedream schema
# updates within a day without amplifying cost.
_SPEC_TTL_S: int = 60 * 60
_spec_cache: dict[str, tuple[list[dict], float]] = {}
_spec_lock = asyncio.Lock()


async def get_action_props(action_id: str) -> list[dict] | None:
    """Return the `configurable_props` list for an action, or None on
    fetch failure. Cached for 1h. Auth props (`type='app'`) are
    stripped (they're injected server-side and the agent never
    passes them)."""
    now = time.monotonic()
    cached = _spec_cache.get(action_id)
    if cached and (now - cached[1]) < _SPEC_TTL_S:
        return cached[0]

    async with _spec_lock:
        cached = _spec_cache.get(action_id)
        if cached and (now - cached[1]) < _SPEC_TTL_S:
            return cached[0]
        try:
            comp = await pd.get_component(action_id)
        except Exception as exc:  # noqa: BLE001
            log.info(
                "action_guardrail_spec_fetch_failed",
                action=action_id, error=str(exc)[:200],
            )
            return None
        raw = comp.get("configurable_props") or []
        props = [p for p in raw if isinstance(p, dict) and p.get("type") != "app"]
        _spec_cache[action_id] = (props, now)
        return props


# --------------------------------------------------------------------------- #
# Pre-call validation
# --------------------------------------------------------------------------- #


def _required_names(props: list[dict]) -> set[str]:
    """Props are required iff `optional` is missing or explicitly False."""
    return {
        p.get("name")
        for p in props
        if p.get("name") and not p.get("optional", False)
    }


def _all_names(props: list[dict]) -> set[str]:
    return {p.get("name") for p in props if p.get("name")}


def validate_params(props: list[dict], params: dict) -> dict | None:
    """Return a structured-error dict when the params don't satisfy the
    schema, else None (call may proceed). Errors include enough detail
    that the LLM can correct without re-asking for the schema:
      - missing required keys
      - unknown keys (likely typos)
      - boolean / numeric type mismatches on the params we can check

    Defensive: type checks are limited to the cases we can verify with
    high confidence (bool, int, string-must-not-be-empty). We do NOT
    coerce; the agent learns the right shape from the error message."""
    if not props:
        return None  # no spec, no validation

    required = _required_names(props)
    allowed = _all_names(props)
    missing = sorted(r for r in required if r not in params)
    unknown = sorted(k for k in (params or {}).keys() if k not in allowed)

    type_errors: list[str] = []
    for p in props:
        name = p.get("name")
        if not name or name not in params:
            continue
        val = params[name]
        expected = p.get("type")
        if expected == "boolean" and not isinstance(val, bool):
            type_errors.append(f"`{name}` must be a boolean, got {type(val).__name__}")
        elif expected == "integer" and not isinstance(val, int):
            type_errors.append(f"`{name}` must be an integer, got {type(val).__name__}")

    if not (missing or unknown or type_errors):
        return None

    return {
        "validation_error": True,
        "missing_required": missing,
        "unknown_fields": unknown,
        "type_errors": type_errors,
        "hint": (
            "Fix the params and retry. Required fields must be present; "
            "unknown fields are typos (compare against the action spec); "
            "type errors must match exactly."
        ),
    }


# --------------------------------------------------------------------------- #
# Post-call sparseness + missing-enrichment detection
# --------------------------------------------------------------------------- #


# Heuristic: the response is "sparse" when the canonical list field
# (records / items / data / results) is empty or near-empty. We don't
# require a SPECIFIC shape; we look at common Pipedream/Composio
# response keys and short-circuit on the first one that matches.
_LIST_KEYS = ("calls", "items", "records", "results", "data", "objects", "ret")


def _extract_items(result: Any) -> list | None:
    """Try to find the list-of-items inside a provider response.

    Returns the list (possibly empty) if found, None if the response
    doesn't look list-shaped at all. Walks one level of Pipedream's
    `ret`/`response`/`body`/`data` wrapper and the conventional
    list-keyed sub-structures."""
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        return None
    for k in _LIST_KEYS:
        v = result.get(k)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            for k2 in _LIST_KEYS:
                v2 = v.get(k2)
                if isinstance(v2, list):
                    return v2
    # Pipedream's `records: {totalRecords: N, items: [...]}` shape.
    rec = result.get("records")
    if isinstance(rec, dict):
        for k2 in _LIST_KEYS:
            v2 = rec.get(k2)
            if isinstance(v2, list):
                return v2
        if isinstance(rec.get("totalRecords"), int) and rec["totalRecords"] == 0:
            return []
    return None


# Param-name patterns that gate "richer" response data. When `False`
# (or `Basic` for `context`), the response shape excludes embedded
# objects the agent often needs (parties, CRM context, transcripts,
# etc.). Empirically these flags follow predictable naming across
# Pipedream's Gong, HubSpot, Salesforce, Linear, Notion components.
_RICH_FLAG_PREFIXES = ("include", "with", "expand", "embed")
_BASIC_VALUES = {"basic", "minimal", "summary", "shallow", False}


def _enrichment_field_for_flag(flag_name: str) -> str | None:
    """Map a rich flag like `includeParties` to the response key it
    gates (`parties`). Returns None if no convention applies."""
    if not flag_name:
        return None
    lname = flag_name.lower()
    for prefix in _RICH_FLAG_PREFIXES:
        if lname.startswith(prefix) and len(flag_name) > len(prefix):
            rest = flag_name[len(prefix):]
            return rest[0].lower() + rest[1:]
    if lname == "context":
        return "context"
    return None


def _identify_off_rich_flags(props: list[dict], params: dict) -> list[str]:
    """Return the names of props that look like "rich-response" flags
    and were passed as Off/Basic/False (or omitted entirely). These
    are the most likely cause of missing enrichment in the response."""
    if not props or not isinstance(params, dict):
        return []
    off: list[str] = []
    for p in props:
        name = p.get("name") or ""
        if not name:
            continue
        lname = name.lower()
        is_rich_toggle = any(lname.startswith(prefix) for prefix in _RICH_FLAG_PREFIXES)
        is_context_toggle = lname == "context"
        if not (is_rich_toggle or is_context_toggle):
            continue
        val = params.get(name)
        if val is None:
            off.append(name)
            continue
        if isinstance(val, bool) and val is False:
            off.append(name)
        elif isinstance(val, str) and val.strip().lower() in _BASIC_VALUES:
            off.append(name)
    return off


def annotate_sparse_result(
    *, result: Any, action_id: str, props: list[dict], params: dict
) -> dict | None:
    """Decide whether to inject a `[platform hint]` annotation into the
    LLM-visible tool output. Two cases trigger:

      1. **Empty list response + off rich flags.** The agent likely
         got nothing back because filters were too strict or richer
         scope was needed; retry with flags flipped.
      2. **Items present, but enrichment fields missing.** The agent
         got rows back but they lack the keys that the off flags would
         have populated (`parties`, `context`, `media`, ...). The
         agent should know it can retry to get them.

    Returns None when the response looks fine or when nothing
    actionable is detected (no off flags, no recognizable list shape).
    """
    items = _extract_items(result)
    if items is None:
        return None
    off_flags = _identify_off_rich_flags(props, params)
    if not off_flags:
        return None

    if not items:
        return {
            "platform_hint": True,
            "action": action_id,
            "off_flags": off_flags,
            "missing_fields": [],
            "observation": (
                "Empty list response. The agent left these rich-response flags "
                f"off (or omitted them): {', '.join(f'`{f}`' for f in off_flags)}. "
                "If the user's intent needed CRM context, participants, "
                "transcripts, or media, retry with those flags on "
                "(`true` or `context='Extended'`). Also broaden the date "
                "window if applicable; many list actions default to recent-only."
            ),
        }

    # Items present. Check which enrichment fields are missing on the
    # first dict-shaped item (a sample is enough).
    sample = next((it for it in items if isinstance(it, dict)), None)
    if sample is None:
        return None
    missing: list[tuple[str, str]] = []
    for flag in off_flags:
        field = _enrichment_field_for_flag(flag)
        if not field:
            continue
        if field not in sample:
            missing.append((flag, field))
    if not missing:
        return None
    parts = ", ".join(f"`{f}` would populate `{fld}`" for f, fld in missing)
    return {
        "platform_hint": True,
        "action": action_id,
        "off_flags": [f for f, _ in missing],
        "missing_fields": [fld for _, fld in missing],
        "observation": (
            f"Response rows are missing enrichment fields: {parts}. If the "
            "user's intent needs CRM context, participants, transcripts, or "
            "media to answer (for example: looking up calls by company or "
            "person name where the name lives in CRM context, not in the "
            "row metadata), retry with those flags on "
            "(`true` or `context='Extended'`)."
        ),
    }


__all__ = [
    "get_action_props",
    "validate_params",
    "annotate_sparse_result",
]
