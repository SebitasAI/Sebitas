"""Skills registry: workspace-scoped uploads, per-user installs, per-user loads.

Tenancy model:

- `skill` rows live in a workspace and are unique by (workspace_id, name).
  Anyone in the workspace can see them; installing is a per-user decision.
- `skill_install` is the per-user opt-in; one user's installs are private to
  that user even from coworkers in the same workspace. Every query in this
  module filters by `user_id` first.

There is no "global catalog" surface anymore: a skill uploaded in workspace A
is invisible to workspace B even if both happen to use the same `name`.

Public surface (used by Slack handlers + the runner + the load_skill tool):

- create_skill(...) -> Skill: persist a new skill from a resolved frontmatter
  + body, including R2 upload. Rejects name collisions explicitly.
- update_skill_body(skill_id, content): replace the body, bump version, keep
  R2 key, invalidate LRU.
- delete_skill(skill_id): drop the skill row + R2 body + cached install rows
  cascade automatically.
- install_for_user(user_id, skill_id, activation_override=None): idempotent.
- uninstall_for_user(user_id, skill_id): removes the install only, not the
  workspace-level skill.
- list_for_user(user_id) -> list[SkillWithInstall]: every install for that
  user, joined with the parent skill row + effective activation pre-computed.
- get_skill_for_user(user_id, name) -> SkillWithInstall | None: name lookup
  for the load_skill tool path. Returns None if not installed by the user.
- load_skill_body_for_user(user_id, name) -> LoadedSkill: full body + warnings
  about cross-references the user has not installed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

import structlog
from sqlalchemy import and_, or_, select

from app.db.models import AppUser, Skill, SkillInstall
from app.db.session import get_session
from app.skills import storage

log = structlog.get_logger(__name__)

Activation = Literal["always_active", "on_demand"]


class SkillError(Exception):
    """Base class for registry errors the Slack layer surfaces back to users."""


class SkillNameTaken(SkillError):
    """Raised when a (workspace_id, name) already exists. The Slack flow
    catches this to force the user into the Edit modal."""


class SkillNotFound(SkillError):
    """Raised when looking up a skill the user doesn't own or that doesn't
    exist in the workspace. Surface to the user as a friendly message."""


@dataclass
class SkillWithInstall:
    """A skill row + the install row (for the user being queried) + the
    effective activation (override winning over default). Used everywhere we
    need to render or filter skills for a user."""

    skill: Skill
    install: SkillInstall
    effective_activation: Activation


@dataclass
class LoadedSkill:
    """Return shape of load_skill_body_for_user. Mirrors the LoadSkillOutput
    schema in the spec (the tool wraps this into a user-facing string)."""

    name: str
    description: str
    body: str
    links: list[str]
    missing_links: list[str]
    warning: str | None


def _effective(activation_default: str, activation_override: str | None) -> Activation:
    return activation_override or activation_default  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Mutations
# --------------------------------------------------------------------------- #


async def create_skill(
    *,
    workspace_id: uuid.UUID,
    name: str,
    description: str,
    activation_default: Activation,
    body: str,
    links: list[str],
    size_bytes: int,
    created_by_user_id: uuid.UUID | None,
    source: str = "upload",
    scope: str = "workspace",
) -> Skill:
    """Persist + upload. Two writes, in this order: insert the skill row to
    reserve (workspace_id, name) atomically; if R2 fails, the row is rolled
    back. Returns the persisted Skill (refreshed).

    `scope` defaults to 'workspace' for back-compat with Slack DM uploads.
    Web upload should pass 'personal' explicitly when the user picks it."""
    async with get_session() as session:
        existing = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace_id, Skill.name == name
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise SkillNameTaken(
                f"ya existe una skill llamada {name!r} en este workspace"
            )

        row = Skill(
            workspace_id=workspace_id,
            name=name,
            description=description,
            body_r2_ref="",  # filled below; the column is NOT NULL so we use ""
            version=1,
            source=source,
            activation_default=activation_default,
            scope=scope,
            links=links,
            size_bytes=size_bytes,
            created_by_user_id=created_by_user_id,
        )
        session.add(row)
        await session.flush()  # need row.id for the R2 key

        try:
            r2_ref = await storage.upload_skill_body(
                workspace_id=workspace_id,
                skill_id=row.id,
                version=row.version,
                content=body,
            )
        except Exception:
            await session.rollback()
            raise
        row.body_r2_ref = r2_ref
        await session.commit()
        await session.refresh(row)

    log.info(
        "skill_uploaded",
        workspace_id=str(workspace_id),
        user_id=str(created_by_user_id) if created_by_user_id else None,
        skill_name=name,
        skill_id=str(row.id),
        size_bytes=size_bytes,
        source=source,
        activation_default=activation_default,
    )
    return row


async def update_skill_body(
    *, skill_id: uuid.UUID, new_body: str, new_size_bytes: int
) -> Skill:
    """Bump version, re-upload to the same R2 key. LRU cache uses version in
    its key so subsequent reads see a miss + re-pop with the new content."""
    async with get_session() as session:
        row = await session.get(Skill, skill_id)
        if row is None:
            raise SkillNotFound(f"skill {skill_id} no encontrada")
        row.version = (row.version or 1) + 1
        row.size_bytes = new_size_bytes
        await storage.upload_skill_body(
            workspace_id=row.workspace_id,
            skill_id=row.id,
            version=row.version,
            content=new_body,
        )
        await session.commit()
        await session.refresh(row)
    log.info(
        "skill_body_updated",
        skill_id=str(skill_id),
        version=row.version,
        size_bytes=new_size_bytes,
    )
    return row


async def delete_skill(*, skill_id: uuid.UUID) -> None:
    """Drop the workspace-level skill. CASCADE removes every install. R2 body
    is deleted best-effort; we tolerate a leaked object so a failed R2 call
    doesn't block the user's intent."""
    async with get_session() as session:
        row = await session.get(Skill, skill_id)
        if row is None:
            return
        workspace_id = row.workspace_id
        r2_ref = row.body_r2_ref
        await session.delete(row)
        await session.commit()
    await storage.delete_skill_body(workspace_id, skill_id, r2_ref)
    log.info("skill_deleted", skill_id=str(skill_id))


async def install_for_user(
    *,
    user_id: uuid.UUID,
    skill_id: uuid.UUID,
    activation_override: Activation | None = None,
) -> SkillInstall:
    """Idempotent: re-installing flips activation_override if it differs.
    Cross-tenant defence: we verify that the user and the skill belong to the
    same workspace before persisting (otherwise it's a programming bug or a
    malicious payload)."""
    async with get_session() as session:
        skill = await session.get(Skill, skill_id)
        user = await session.get(AppUser, user_id)
        if skill is None or user is None:
            raise SkillNotFound("skill o usuario no existe")
        if skill.workspace_id != user.workspace_id:
            raise SkillError(
                "no se puede instalar una skill de otro workspace"
            )

        existing = (
            await session.execute(
                select(SkillInstall).where(
                    SkillInstall.user_id == user_id,
                    SkillInstall.skill_id == skill_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.activation_override != activation_override:
                existing.activation_override = activation_override
                await session.commit()
                await session.refresh(existing)
            return existing
        row = SkillInstall(
            user_id=user_id, skill_id=skill_id, activation_override=activation_override
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    log.info(
        "skill_installed",
        user_id=str(user_id),
        skill_id=str(skill_id),
        activation_override=activation_override,
    )
    return row


async def uninstall_for_user(*, user_id: uuid.UUID, skill_id: uuid.UUID) -> None:
    """Removes the install row only. The workspace-level skill stays so other
    users who installed it keep it. Idempotent."""
    async with get_session() as session:
        row = (
            await session.execute(
                select(SkillInstall).where(
                    SkillInstall.user_id == user_id,
                    SkillInstall.skill_id == skill_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return
        await session.delete(row)
        await session.commit()
    log.info("skill_uninstalled", user_id=str(user_id), skill_id=str(skill_id))


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #


async def list_for_user(user_id: uuid.UUID) -> list[SkillWithInstall]:
    """Every install for the user, joined to the skill row, sorted by install
    recency. Eager: a user with hundreds of skills is rare; if it becomes a
    problem, paginate at the Slack layer."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Skill, SkillInstall)
                .join(SkillInstall, SkillInstall.skill_id == Skill.id)
                .where(SkillInstall.user_id == user_id)
                .order_by(SkillInstall.installed_at.desc())
            )
        ).all()
    out: list[SkillWithInstall] = []
    for skill, install in rows:
        out.append(
            SkillWithInstall(
                skill=skill,
                install=install,
                effective_activation=_effective(
                    skill.activation_default, install.activation_override
                ),
            )
        )
    return out


async def list_for_workspace(workspace_id: uuid.UUID) -> list[Skill]:
    """All workspace skills regardless of install status. Used by the Slack
    catalogue-style flows (future) and admin tools; the per-user flow uses
    `list_for_user` instead.

    Note: returns every row in the workspace including personal ones. The
    per-user visibility filter belongs in `list_visible_for_user` (which
    the web /api/skills uses); callers that want admin-style "show me
    every skill" still use this function."""
    async with get_session() as session:
        return list(
            (
                await session.execute(
                    select(Skill)
                    .where(Skill.workspace_id == workspace_id)
                    .order_by(Skill.created_at.desc())
                )
            ).scalars()
        )


async def list_visible_for_user(
    workspace_id: uuid.UUID, user_id: uuid.UUID
) -> list[Skill]:
    """Skills the user is allowed to see: every workspace-scope skill plus
    the user's own personal skills. Used by the web /api/skills endpoint
    and the agent's list_workspace_skills tool.

    A personal skill is only visible to its creator (`created_by_user_id`).
    A workspace skill is visible to all members of that workspace. Cross-
    workspace isolation is enforced by the `workspace_id` filter."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace_id,
                    or_(
                        Skill.scope == "workspace",
                        and_(
                            Skill.scope == "personal",
                            Skill.created_by_user_id == user_id,
                        ),
                    ),
                ).order_by(Skill.created_at.desc())
            )
        ).scalars().all()
    return list(rows)


async def list_installable_for_user(user_id: uuid.UUID) -> list[Skill]:
    """Workspace skills the user could install but hasn't yet. Drives the
    'browse mode' of `/misterr skill install` (no args). One query: SELECT
    workspace skills WHERE id NOT IN (this user's installs)."""
    async with get_session() as session:
        user = await session.get(AppUser, user_id)
        if user is None:
            return []
        already_installed = (
            select(SkillInstall.skill_id)
            .where(SkillInstall.user_id == user_id)
            .scalar_subquery()
        )
        rows = (
            await session.execute(
                select(Skill)
                .where(
                    Skill.workspace_id == user.workspace_id,
                    Skill.id.notin_(already_installed),
                )
                .order_by(Skill.created_at.desc())
            )
        ).scalars().all()
    return list(rows)


async def get_skill_for_user(
    user_id: uuid.UUID, name: str
) -> SkillWithInstall | None:
    """Resolve `name` to the skill row, restricted to skills the user has
    installed. Returns None if not installed (the load_skill tool surfaces
    that to the model as a friendly error)."""
    async with get_session() as session:
        result = (
            await session.execute(
                select(Skill, SkillInstall)
                .join(SkillInstall, SkillInstall.skill_id == Skill.id)
                .where(
                    SkillInstall.user_id == user_id,
                    Skill.name == name,
                )
            )
        ).first()
    if result is None:
        return None
    skill, install = result
    return SkillWithInstall(
        skill=skill,
        install=install,
        effective_activation=_effective(
            skill.activation_default, install.activation_override
        ),
    )


async def get_skill_in_workspace(
    workspace_id: uuid.UUID, name: str
) -> Skill | None:
    """Workspace-level lookup, used when we need to install a skill someone
    else uploaded. No install relationship implied."""
    async with get_session() as session:
        return (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace_id, Skill.name == name
                )
            )
        ).scalar_one_or_none()


async def load_skill_body_for_user(
    user_id: uuid.UUID, name: str, *, thread_id: str | None = None
) -> LoadedSkill:
    """Fetch the full body for the agent's `load_skill` tool. Cross-reference
    handling per spec: returns the list of `[[link]]` slugs the user has NOT
    installed, plus a structured-log event per missing link so analytics can
    tell us which bundles to surface next."""
    swi = await get_skill_for_user(user_id, name)
    if swi is None:
        raise SkillNotFound(f"Skill {name!r} no instalada para este usuario.")
    body = await storage.download_skill_body(
        workspace_id=swi.skill.workspace_id,
        skill_id=swi.skill.id,
        version=swi.skill.version,
        r2_ref=swi.skill.body_r2_ref,
    )

    links = list(swi.skill.links or [])
    missing: list[str] = []
    if links:
        async with get_session() as session:
            rows = (
                await session.execute(
                    select(Skill.name)
                    .join(SkillInstall, SkillInstall.skill_id == Skill.id)
                    .where(
                        SkillInstall.user_id == user_id,
                        Skill.name.in_(links),
                    )
                )
            ).all()
        installed = {r[0] for r in rows}
        missing = [link for link in links if link not in installed]

    log.info(
        "skill_loaded",
        user_id=str(user_id),
        skill_id=str(swi.skill.id),
        skill_name=swi.skill.name,
        thread_id=thread_id,
        size_bytes=swi.skill.size_bytes,
        source="load_skill_tool",
    )
    for link in missing:
        log.info(
            "skill_missing_link_referenced",
            user_id=str(user_id),
            loaded_skill_name=swi.skill.name,
            missing_link_name=link,
        )

    warning = None
    if missing:
        formatted = ", ".join(f"`{m}`" for m in missing)
        warning = (
            f"Esta skill referencia skills no instaladas: {formatted}. "
            "Si las necesitás, pedile al usuario que las instale; no las "
            "auto-cargo."
        )
    return LoadedSkill(
        name=swi.skill.name,
        description=swi.skill.description,
        body=body,
        links=links,
        missing_links=missing,
        warning=warning,
    )
