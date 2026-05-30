"""REST endpoints for the Misterr web app's Skills page (slice T-6).

Auth: same Clerk-JWT chain as scheduled-tasks. Every handler resolves to a
single AppUser via `require_app_user` so the workspace and user filters
fall out of the auth layer.

v1 surface: list (workspace + own personal skills with install state),
install, uninstall. Upload of new skills stays in Slack DM for now.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.clerk import ResolvedAppUser, require_app_user
from app.db.models import Skill, SkillInstall
from app.db.session import get_session
from app.skills import registry as skill_registry

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/skills", tags=["skills"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #


class SkillOut(BaseModel):
    id: str
    name: str
    description: str
    scope: Literal["workspace", "personal"]
    activation_default: Literal["always_active", "on_demand"]
    activation_override: Literal["always_active", "on_demand"] | None
    effective_activation: Literal["always_active", "on_demand"]
    source: str
    version: int
    links: list[str]
    size_bytes: int
    created_at: datetime
    created_by_user_id: str | None
    is_installed: bool
    is_mine: bool


class SkillListResponse(BaseModel):
    skills: list[SkillOut]
    total_count: int


def _effective_activation(
    skill_default: str, override: str | None
) -> Literal["always_active", "on_demand"]:
    if override in ("always_active", "on_demand"):
        return override  # type: ignore[return-value]
    return skill_default  # type: ignore[return-value]


def _serialize(
    skill: Skill,
    install: SkillInstall | None,
    current_user_id,
) -> SkillOut:
    return SkillOut(
        id=str(skill.id),
        name=skill.name,
        description=skill.description,
        scope=skill.scope,  # type: ignore[arg-type]
        activation_default=skill.activation_default,  # type: ignore[arg-type]
        activation_override=install.activation_override if install else None,  # type: ignore[arg-type]
        effective_activation=_effective_activation(
            skill.activation_default,
            install.activation_override if install else None,
        ),
        source=skill.source,
        version=skill.version,
        links=skill.links or [],
        size_bytes=skill.size_bytes,
        created_at=skill.created_at,
        created_by_user_id=str(skill.created_by_user_id) if skill.created_by_user_id else None,
        is_installed=install is not None,
        is_mine=skill.created_by_user_id == current_user_id,
    )


# --------------------------------------------------------------------------- #
# GET /api/skills
# --------------------------------------------------------------------------- #


@router.get("", response_model=SkillListResponse)
async def list_skills(
    user: ResolvedAppUser = Depends(require_app_user),
) -> SkillListResponse:
    """Return every skill visible to the caller: workspace-scope skills
    plus the caller's own personal skills. Each row carries install state
    so the UI can render Install / Uninstall buttons accordingly."""
    visible = await skill_registry.list_visible_for_user(
        user.workspace_id, user.app_user_id
    )
    skill_ids = [s.id for s in visible]
    installs_by_skill: dict = {}
    if skill_ids:
        async with get_session() as session:
            rows = (
                await session.execute(
                    select(SkillInstall).where(
                        SkillInstall.user_id == user.app_user_id,
                        SkillInstall.skill_id.in_(skill_ids),
                    )
                )
            ).scalars().all()
            for inst in rows:
                installs_by_skill[inst.skill_id] = inst

    serialized = [
        _serialize(s, installs_by_skill.get(s.id), user.app_user_id)
        for s in visible
    ]
    return SkillListResponse(skills=serialized, total_count=len(serialized))


# --------------------------------------------------------------------------- #
# POST /api/skills/{name}/install
# --------------------------------------------------------------------------- #


@router.post("/{name}/install", response_model=SkillOut)
async def install_skill(
    name: str,
    user: ResolvedAppUser = Depends(require_app_user),
) -> SkillOut:
    """Install the named skill for the calling user. Personal skills owned
    by another user surface as 404 -- they don't exist from the caller's
    POV (we don't want to leak their existence either)."""
    skill = await _resolve_visible_skill(name, user)
    install = await skill_registry.install_for_user(
        user_id=user.app_user_id, skill_id=skill.id
    )
    return _serialize(skill, install, user.app_user_id)


# --------------------------------------------------------------------------- #
# POST /api/skills/{name}/uninstall
# --------------------------------------------------------------------------- #


@router.post("/{name}/uninstall", response_model=SkillOut)
async def uninstall_skill(
    name: str,
    user: ResolvedAppUser = Depends(require_app_user),
) -> SkillOut:
    skill = await _resolve_visible_skill(name, user)
    install_row = await skill_registry.get_skill_for_user(user.app_user_id, name)
    if install_row is None:
        return _serialize(skill, None, user.app_user_id)
    await skill_registry.uninstall_for_user(
        user_id=user.app_user_id, skill_id=skill.id
    )
    return _serialize(skill, None, user.app_user_id)


async def _resolve_visible_skill(name: str, user: ResolvedAppUser) -> Skill:
    """Look up the named skill in the caller's workspace, enforcing the
    personal-skill visibility rule. Raises 404 when the skill doesn't
    exist OR exists as a personal skill of someone else."""
    async with get_session() as session:
        row = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == user.workspace_id,
                    Skill.name == name,
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"skill `{name}` not found")
    if row.scope == "personal" and row.created_by_user_id != user.app_user_id:
        raise HTTPException(status_code=404, detail=f"skill `{name}` not found")
    return row


__all__ = ["router"]
