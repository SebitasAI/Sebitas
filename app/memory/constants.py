"""Reserved skill slugs for workspace memory + slug helpers."""

from __future__ import annotations

COMPANY_SLUG = "company"
TEAM_SLUG = "team"
USER_SLUG_PREFIX = "users/"
# Channel-scoped memory: one skill per Slack channel-like entity (public
# channels, private channels, mpims). 1:1 DMs are NOT given a separate
# channel skill -- by definition there's one human in a 1:1 DM, so the
# user's `users/<id>` memory is the natural home and a parallel
# `channels/D...` would just duplicate.
CHANNEL_SLUG_PREFIX = "channels/"


def user_slug(slack_user_id: str) -> str:
    """Canonical per-user memory slug. Slack U-ids are case-insensitive
    in practice but tooling normalizes them to lower case to avoid
    accidental dupes (`users/U123` vs `users/u123`). The slug column in
    `skill` is workspace-unique, so we want a single canonical form."""
    return f"{USER_SLUG_PREFIX}{slack_user_id.lower()}"


def channel_slug(slack_channel_id: str) -> str:
    """Canonical per-channel memory slug. Slack channel IDs are
    case-insensitive in practice; we lowercase for the same reasons we
    lowercase user slugs."""
    return f"{CHANNEL_SLUG_PREFIX}{slack_channel_id.lower()}"


def is_one_to_one_dm(slack_channel_id: str) -> bool:
    """Slack channel IDs that start with 'D' are 1:1 DMs. Public/private
    channels are 'C' / 'G', mpims are 'G' (legacy) or 'C' for new shared
    style. The 'D' prefix is reliable across all Slack workspace
    generations for 1:1 DMs.

    Used to decide whether to load a `channels/<id>` memory skill on a
    given turn -- 1:1 DMs skip it (the user skill covers them)."""
    return bool(slack_channel_id) and slack_channel_id.upper().startswith("D")


def is_memory_skill_name(name: str) -> bool:
    """True when `name` matches one of the reserved memory slugs.
    Used to filter memory skills out of the user-facing skill listing
    (`/api/skills`) and to gate auto-load in the prompt builder."""
    return (
        name == COMPANY_SLUG
        or name == TEAM_SLUG
        or name.startswith(USER_SLUG_PREFIX)
        or name.startswith(CHANNEL_SLUG_PREFIX)
    )


__all__ = [
    "COMPANY_SLUG",
    "TEAM_SLUG",
    "USER_SLUG_PREFIX",
    "CHANNEL_SLUG_PREFIX",
    "user_slug",
    "channel_slug",
    "is_one_to_one_dm",
    "is_memory_skill_name",
]
