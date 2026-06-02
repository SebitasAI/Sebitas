"""Reserved skill slugs for workspace memory + slug helpers."""

from __future__ import annotations

COMPANY_SLUG = "company"
TEAM_SLUG = "team"
USER_SLUG_PREFIX = "users/"


def user_slug(slack_user_id: str) -> str:
    """Canonical per-user memory slug. Slack U-ids are case-insensitive
    in practice but tooling normalizes them to lower case to avoid
    accidental dupes (`users/U123` vs `users/u123`). The slug column in
    `skill` is workspace-unique, so we want a single canonical form."""
    return f"{USER_SLUG_PREFIX}{slack_user_id.lower()}"


def is_memory_skill_name(name: str) -> bool:
    """True when `name` matches one of the reserved memory slugs.
    Used to filter memory skills out of the user-facing skill listing
    (`/api/skills`) and to gate auto-load in the prompt builder."""
    return name == COMPANY_SLUG or name == TEAM_SLUG or name.startswith(USER_SLUG_PREFIX)


__all__ = [
    "COMPANY_SLUG",
    "TEAM_SLUG",
    "USER_SLUG_PREFIX",
    "user_slug",
    "is_memory_skill_name",
]
