"""Timezone resolution for scheduled tasks.

Users type things like "hora Col" or "PT" when scheduling; this helper maps
those to IANA names that croniter + zoneinfo understand. Falls back to the
user's Slack profile tz (cached in SlackUser.tz when we add it), and finally
to UTC with a structlog warning so we can audit which inputs need new aliases.

The agent SHOULD pass `fallback_slack_tz` from the calling user's Slack
profile when invoking `create_scheduled_task`; the tool layer pulls this
from the agent context.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

import structlog

log = structlog.get_logger(__name__)


# Spanish + colloquial English aliases. Lowercased keys; lookup lowercases the
# input. Keep the list focused (LatAm + common US/EU); extend as we see misses
# in the `scheduled_task_timezone_fallback` log channel.
_ALIASES: dict[str, str] = {
    "hora col": "America/Bogota",
    "hora colombia": "America/Bogota",
    "hora colombiana": "America/Bogota",
    "col": "America/Bogota",
    "bogota": "America/Bogota",
    "bogotá": "America/Bogota",
    "est": "America/New_York",
    "edt": "America/New_York",
    "et": "America/New_York",
    "eastern": "America/New_York",
    "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "pt": "America/Los_Angeles",
    "pacific": "America/Los_Angeles",
    "ct": "America/Chicago",
    "central": "America/Chicago",
    "mt": "America/Denver",
    "mountain": "America/Denver",
    "hora mex": "America/Mexico_City",
    "hora mexico": "America/Mexico_City",
    "hora méxico": "America/Mexico_City",
    "mex": "America/Mexico_City",
    "cdmx": "America/Mexico_City",
    "hora br": "America/Sao_Paulo",
    "hora brasil": "America/Sao_Paulo",
    "brt": "America/Sao_Paulo",
    "sao paulo": "America/Sao_Paulo",
    "são paulo": "America/Sao_Paulo",
    "hora arg": "America/Argentina/Buenos_Aires",
    "hora argentina": "America/Argentina/Buenos_Aires",
    "art": "America/Argentina/Buenos_Aires",
    "buenos aires": "America/Argentina/Buenos_Aires",
    "uk": "Europe/London",
    "london": "Europe/London",
    "bst": "Europe/London",
    "gmt": "UTC",  # GMT and UTC differ in some pedantic senses; for cron use they're identical.
    "madrid": "Europe/Madrid",
    "berlin": "Europe/Berlin",
    "berlín": "Europe/Berlin",
    "utc": "UTC",
}


def _is_valid_iana(name: str) -> bool:
    """True if `name` is a real IANA timezone. `available_timezones()` is
    cached after the first call (cpython 3.9+)."""
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return False
    return True


def resolve_timezone(
    text: str | None,
    fallback_slack_tz: str | None = None,
    *,
    task_id: str | None = None,
) -> str:
    """Return a valid IANA timezone name for the user's input.

    Resolution order:
      1. `text` is an IANA name (case-insensitive direct lookup, then exact).
      2. `text` matches a colloquial alias above (lowercased).
      3. `fallback_slack_tz` is a valid IANA name (the Slack profile tz).
      4. UTC (and we log it so we can grow the alias table).

    The `task_id` arg is purely for structlog correlation; pass it when you
    have one. The function NEVER raises; the worst case is a logged fallback
    to UTC so the task can still fire (even if at the wrong hour).
    """
    if text:
        stripped = text.strip()
        lowered = stripped.lower()
        # Always try the canonical capitalization FIRST -- macOS's case-insensitive
        # filesystem makes raw lowercase pass _is_valid_iana locally but fail in
        # production (Linux case-sensitive). Canonicalize so the stored tz is
        # portable.
        canonical_attempt = "/".join(p.capitalize() for p in lowered.split("/"))
        if _is_valid_iana(canonical_attempt):
            return canonical_attempt
        if _is_valid_iana(stripped):
            return stripped
        if lowered in _ALIASES:
            return _ALIASES[lowered]

    if fallback_slack_tz and _is_valid_iana(fallback_slack_tz):
        return fallback_slack_tz

    log.warning(
        "scheduled_task_timezone_fallback",
        task_id=task_id,
        requested_tz=text,
        slack_fallback=fallback_slack_tz,
        used_tz="UTC",
        reason="no_alias_match",
    )
    return "UTC"


def list_known_aliases() -> dict[str, str]:
    """For diagnostics + tests: copy of the alias map."""
    return dict(_ALIASES)


__all__ = ["resolve_timezone", "list_known_aliases"]


# Sanity check at import time: every alias value should be a real IANA name.
# Cheap (a couple dozen ZoneInfo() constructions), surfaces typos in this
# file fast rather than when a user happens to type the broken alias.
_known = available_timezones()
for _alias, _target in _ALIASES.items():
    if _target not in _known:
        raise RuntimeError(
            f"app/scheduled_tasks/timezone.py: alias {_alias!r} -> {_target!r} "
            "is not a valid IANA timezone. Fix the alias table."
        )
del _alias, _target, _known
