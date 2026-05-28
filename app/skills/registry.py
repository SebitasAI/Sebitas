"""Skill registry: register packages, install/uninstall per workspace, and load
them dynamically at runtime (progressive: descriptions always, SKILL.md on demand).

A skill package lives in R2 under `skills/{name}/{version}/` (SKILL.md +
manifest.json + resources); the DB row holds metadata + that prefix
(`manifest_ref`). Nothing here is hardcoded; the demo skill is seeded through
register_skill + install, exactly like any other skill.
"""

from __future__ import annotations

import json
import uuid

import structlog
from sqlalchemy import select

from app.agent.context import workspace_id_var
from app.artifacts import r2
from app.db.models import Skill, SkillInstall
from app.db.session import get_session

log = structlog.get_logger(__name__)


async def register_skill(manifest: dict, skill_md: str, resources: dict[str, bytes] | None = None) -> str:
    """Upload a skill package to R2 and upsert its registry row. Returns the name."""
    name = manifest["name"]
    version = manifest.get("version", "0.1.0")
    prefix = f"skills/{name}/{version}/"

    await r2.put_bytes(prefix + "manifest.json", json.dumps(manifest, ensure_ascii=False).encode(), "application/json")
    await r2.put_bytes(prefix + "SKILL.md", skill_md.encode(), "text/markdown")
    for fname, data in (resources or {}).items():
        await r2.put_bytes(prefix + "resources/" + fname, data)

    async with get_session() as session:
        existing = (await session.execute(select(Skill).where(Skill.name == name))).scalar_one_or_none()
        if existing is None:
            existing = Skill(name=name)
            session.add(existing)
        existing.description = manifest.get("description", "")
        existing.tags = manifest.get("tags")
        existing.author = manifest.get("author")
        existing.category = manifest.get("category")
        existing.version = version
        existing.manifest_ref = prefix
        await session.commit()
    log.info("skill_registered", name=name, version=version)
    return name


async def install(workspace_id: uuid.UUID, skill_name: str) -> None:
    async with get_session() as session:
        skill = (await session.execute(select(Skill).where(Skill.name == skill_name))).scalar_one_or_none()
        if skill is None:
            raise ValueError(f"skill {skill_name!r} no está en el registro")
        existing = (
            await session.execute(
                select(SkillInstall).where(
                    SkillInstall.workspace_id == workspace_id, SkillInstall.skill_id == skill.id
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(SkillInstall(workspace_id=workspace_id, skill_id=skill.id, version=skill.version, enabled=True))
        else:
            existing.enabled = True
            existing.version = skill.version
        await session.commit()
    log.info("skill_installed", workspace_id=str(workspace_id), skill=skill_name)


async def uninstall(workspace_id: uuid.UUID, skill_name: str) -> None:
    async with get_session() as session:
        skill = (await session.execute(select(Skill).where(Skill.name == skill_name))).scalar_one_or_none()
        if skill is None:
            return
        install_row = (
            await session.execute(
                select(SkillInstall).where(
                    SkillInstall.workspace_id == workspace_id, SkillInstall.skill_id == skill.id
                )
            )
        ).scalar_one_or_none()
        if install_row is not None:
            install_row.enabled = False
            await session.commit()
    log.info("skill_uninstalled", workspace_id=str(workspace_id), skill=skill_name)


async def list_installed(workspace_id: uuid.UUID) -> list[Skill]:
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Skill)
                .join(SkillInstall, SkillInstall.skill_id == Skill.id)
                .where(SkillInstall.workspace_id == workspace_id, SkillInstall.enabled.is_(True))
            )
        ).scalars().all()
    return list(rows)


async def installed_descriptions_text(workspace_id: uuid.UUID) -> str:
    """Compact list of installed skills for the model (progressive loading: only
    descriptions go to context; SKILL.md is fetched on demand via load_skill)."""
    skills = await list_installed(workspace_id)
    if not skills:
        return ""
    lines = "\n".join(f"• {s.name} — {s.description}" for s in skills)
    return (
        "Skills instaladas en este workspace. Cuando una aplique a la tarea, llamá "
        "la tool `load_skill` con su nombre para cargar sus instrucciones completas:\n"
        f"{lines}"
    )


async def load_skill_md(name: str) -> str:
    """Fetch the full SKILL.md of an installed skill for the current workspace.
    Hook: when the library grows, this is where semantic skill-search (pgvector)
    would narrow candidates before loading."""
    ws = workspace_id_var.get()
    if not ws:
        return "Error: sin contexto de workspace."
    workspace_id = uuid.UUID(ws)
    async with get_session() as session:
        skill = (
            await session.execute(
                select(Skill)
                .join(SkillInstall, SkillInstall.skill_id == Skill.id)
                .where(
                    Skill.name == name,
                    SkillInstall.workspace_id == workspace_id,
                    SkillInstall.enabled.is_(True),
                )
            )
        ).scalar_one_or_none()
    if skill is None:
        return f"La skill {name!r} no está instalada en este workspace."
    try:
        return await r2.get_text(skill.manifest_ref + "SKILL.md")
    except Exception as exc:  # noqa: BLE001
        log.warning("skill_md_load_failed", name=name, error=str(exc))
        return f"No pude cargar el SKILL.md de {name!r}: {exc}"
