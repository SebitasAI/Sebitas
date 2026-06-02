"""Idempotent seeding of the three memory skills per workspace / user.

Called from:
  - `MisterrInstallationStore.async_save` (workspace install) -> company + team
  - `runner._persist_user` (first message of a new user) -> users/<slack_id>

These never throw. A failure to seed must never block the user-visible
flow that triggered it; we log + return the existing row when possible.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select

from app.db.models import Skill
from app.db.session import get_session
from app.memory.constants import COMPANY_SLUG, TEAM_SLUG, user_slug
from app.skills import registry as skill_registry

log = structlog.get_logger(__name__)


# Initial body template. The "Curated summary" section is what compaction
# (Phase C) will eventually rewrite from the observations log; we put a
# placeholder line so the agent has something to read even before any
# observations exist. Keep this terse -- it's loaded into the system
# prompt every turn.
def _initial_body(scope_label: str) -> str:
    when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"<!-- bootstrapped: {when} -->\n"
        "## Curated summary\n"
        f"(no information yet about this {scope_label})\n"
        "\n"
        "## Observations log\n"
    )


async def _existing(workspace_id: uuid.UUID, name: str) -> Skill | None:
    async with get_session() as session:
        return (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace_id,
                    Skill.name == name,
                )
            )
        ).scalar_one_or_none()


async def ensure_company_skill(workspace_id: uuid.UUID) -> Skill:
    """Idempotent: returns the existing `company` skill, creates the stub
    if missing. Logs `memory_skill_seeded` on creation only."""
    row = await _existing(workspace_id, COMPANY_SLUG)
    if row is not None:
        return row
    body = _initial_body("company")
    row = await skill_registry.create_skill(
        workspace_id=workspace_id,
        name=COMPANY_SLUG,
        description="Workspace-level company memory: product, market, stage, tools.",
        activation_default="always_active",
        body=body,
        links=[],
        size_bytes=len(body.encode("utf-8")),
        created_by_user_id=None,
        source="memory",
        scope="workspace",
    )
    log.info(
        "memory_skill_seeded",
        workspace_id=str(workspace_id),
        skill_name=COMPANY_SLUG,
        by="install",
    )
    return row


async def ensure_team_skill(workspace_id: uuid.UUID) -> Skill:
    row = await _existing(workspace_id, TEAM_SLUG)
    if row is not None:
        return row
    body = _initial_body("team")
    row = await skill_registry.create_skill(
        workspace_id=workspace_id,
        name=TEAM_SLUG,
        description="Workspace-level team memory: who-is-who, roles, channels.",
        activation_default="always_active",
        body=body,
        links=[],
        size_bytes=len(body.encode("utf-8")),
        created_by_user_id=None,
        source="memory",
        scope="workspace",
    )
    log.info(
        "memory_skill_seeded",
        workspace_id=str(workspace_id),
        skill_name=TEAM_SLUG,
        by="install",
    )
    return row


async def ensure_user_skill(
    workspace_id: uuid.UUID,
    app_user_id: uuid.UUID,
    slack_user_id: str,
) -> Skill | None:
    """Per-user memory stub. Created lazily on the user's first message
    (NOT at roster sync -- many SlackUsers never interact with Misterr
    and creating stubs for them is wasted storage).

    `created_by_user_id=app_user_id` so the scope=personal visibility
    rule (registry.list_visible_for_user) only shows this skill to the
    owner. Other users in the workspace cannot see it.

    Returns None on failure -- callers must not treat the absence of a
    user skill as fatal. The agent still gets company + team in context.
    """
    slug = user_slug(slack_user_id)
    try:
        existing = await _existing(workspace_id, slug)
        if existing is not None:
            return existing
        body = _initial_body("user")
        row = await skill_registry.create_skill(
            workspace_id=workspace_id,
            name=slug,
            description=f"Personal memory for Slack user {slack_user_id}.",
            activation_default="always_active",
            body=body,
            links=[],
            size_bytes=len(body.encode("utf-8")),
            created_by_user_id=app_user_id,
            source="memory",
            scope="personal",
        )
        log.info(
            "memory_skill_seeded",
            workspace_id=str(workspace_id),
            skill_name=slug,
            by="first_message",
        )
        return row
    except Exception as exc:  # noqa: BLE001
        # Best-effort; never block the run that triggered this.
        log.warning(
            "memory_user_skill_seed_failed",
            workspace_id=str(workspace_id),
            slack_user_id=slack_user_id,
            error=str(exc)[:200],
        )
        return None


__all__ = [
    "ensure_company_skill",
    "ensure_team_skill",
    "ensure_user_skill",
]
