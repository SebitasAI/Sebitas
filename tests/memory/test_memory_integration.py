"""Integration tests for the workspace memory subsystem (slice T-X Phase A).

Covers:
  - `seed.ensure_company_skill` + `ensure_team_skill` + `ensure_user_skill`
    are idempotent and write the expected scope/source/owner shape.
  - `append.append_observation` writes a bullet to `## Observations log`,
    inserts the header when missing, and respects the per-observation cap.
  - `prompt_builder.build_skills_context` injects `<memory scope="...">`
    blocks for company / team / users/<id> when `workspace_id` +
    `slack_user_id` are provided.
  - `remember` agent tool routes by scope and appends to the right skill.

Requires TEST_DATABASE_URL (auto-skipped otherwise via the db_session
fixture in conftest.py).
"""

from __future__ import annotations

import uuid

import pytest

from app.agent import memory_tools as _memory_tools  # noqa: F401 ensure tool registered
from app.agent.context import app_user_id_var, workspace_id_var
from app.agent.tools import get_tool
from app.db.models import AppUser, Skill
from app.db.session import get_session
from app.memory import append, seed
from app.memory.constants import COMPANY_SLUG, TEAM_SLUG, user_slug
from app.skills import prompt_builder
from sqlalchemy import select


pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Seed
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ensure_company_skill_idempotent(fake_r2, db_session, workspace):
    first = await seed.ensure_company_skill(workspace.id)
    second = await seed.ensure_company_skill(workspace.id)
    assert first.id == second.id
    assert first.name == COMPANY_SLUG
    assert first.source == "memory"
    assert first.scope == "workspace"
    assert first.activation_default == "always_active"

    async with get_session() as session:
        count = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace.id,
                    Skill.name == COMPANY_SLUG,
                )
            )
        ).scalars().all()
    assert len(count) == 1


@pytest.mark.asyncio
async def test_ensure_team_skill_idempotent(fake_r2, db_session, workspace):
    first = await seed.ensure_team_skill(workspace.id)
    second = await seed.ensure_team_skill(workspace.id)
    assert first.id == second.id
    assert first.name == TEAM_SLUG
    assert first.source == "memory"
    assert first.scope == "workspace"


@pytest.mark.asyncio
async def test_ensure_user_skill_personal_scope(fake_r2, db_session, workspace, user_a):
    row = await seed.ensure_user_skill(workspace.id, user_a.id, user_a.slack_user_id)
    assert row is not None
    assert row.name == user_slug(user_a.slack_user_id)
    assert row.scope == "personal"
    assert row.created_by_user_id == user_a.id
    assert row.source == "memory"

    # Idempotent.
    again = await seed.ensure_user_skill(workspace.id, user_a.id, user_a.slack_user_id)
    assert again is not None and again.id == row.id


@pytest.mark.asyncio
async def test_user_slug_lowercases(fake_r2, db_session, workspace):
    user = AppUser(workspace_id=workspace.id, slack_user_id="U_MIXED_Case_123")
    db_session.add(user)
    await db_session.flush()
    row = await seed.ensure_user_skill(workspace.id, user.id, user.slack_user_id)
    assert row is not None
    assert row.name == f"users/u_mixed_case_123"


# --------------------------------------------------------------------------- #
# Append
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_append_observation_writes_bullet(fake_r2, db_session, workspace):
    await seed.ensure_company_skill(workspace.id)
    ok = await append.append_observation(
        workspace.id,
        COMPANY_SLUG,
        text="Antiff vende chargebacks",
        source="explicit-remember",
    )
    assert ok is True

    # Re-read the body via the storage layer (going through registry to
    # match how prompt_builder loads it).
    from app.skills import storage as skill_storage

    async with get_session() as session:
        skill = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace.id,
                    Skill.name == COMPANY_SLUG,
                )
            )
        ).scalar_one()
    body = await skill_storage.download_skill_body(
        workspace_id=skill.workspace_id,
        skill_id=skill.id,
        version=skill.version,
        r2_ref=skill.body_r2_ref,
    )
    assert "## Observations log" in body
    assert "[explicit-remember]: Antiff vende chargebacks" in body


@pytest.mark.asyncio
async def test_append_observation_inserts_header_when_missing(
    fake_r2, db_session, workspace
):
    """If a memory body has been hand-edited and the log header is gone, the
    append code must re-create the section so subsequent reads still parse."""
    from app.skills import registry, storage as skill_storage

    # Create a memory skill with a body that has NO `## Observations log`.
    body = "## Curated summary\n(only curated text, no log section here)\n"
    skill = await registry.create_skill(
        workspace_id=workspace.id,
        name=COMPANY_SLUG,
        description="d",
        activation_default="always_active",
        body=body,
        links=[],
        size_bytes=len(body.encode("utf-8")),
        created_by_user_id=None,
        source="memory",
        scope="workspace",
    )

    ok = await append.append_observation(
        workspace.id,
        COMPANY_SLUG,
        text="Equipo es 100% remoto",
        source="explicit-remember",
    )
    assert ok is True

    async with get_session() as session:
        refreshed = await session.get(Skill, skill.id)
    out = await skill_storage.download_skill_body(
        workspace_id=refreshed.workspace_id,
        skill_id=refreshed.id,
        version=refreshed.version,
        r2_ref=refreshed.body_r2_ref,
    )
    assert "## Curated summary" in out
    assert "## Observations log" in out
    assert "Equipo es 100% remoto" in out


@pytest.mark.asyncio
async def test_append_observation_returns_false_when_skill_missing(
    fake_r2, db_session, workspace
):
    ok = await append.append_observation(
        workspace.id,
        "company",
        text="x",
        source="explicit-remember",
    )
    # No seed -> no row -> false, but no exception.
    assert ok is False


@pytest.mark.asyncio
async def test_append_truncates_oversized_observation(fake_r2, db_session, workspace):
    await seed.ensure_company_skill(workspace.id)
    text = "x" * (append.MAX_OBSERVATION_CHARS + 200)
    ok = await append.append_observation(
        workspace.id, COMPANY_SLUG, text=text, source="explicit-remember"
    )
    assert ok is True

    from app.skills import storage as skill_storage

    async with get_session() as session:
        skill = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace.id,
                    Skill.name == COMPANY_SLUG,
                )
            )
        ).scalar_one()
    body = await skill_storage.download_skill_body(
        workspace_id=skill.workspace_id,
        skill_id=skill.id,
        version=skill.version,
        r2_ref=skill.body_r2_ref,
    )
    # The appended bullet should contain at most MAX_OBSERVATION_CHARS + an
    # ellipsis marker. Check the line is bounded.
    log_lines = [line for line in body.splitlines() if "[explicit-remember]:" in line]
    assert log_lines, "expected at least one observation line"
    payload = log_lines[-1].split("[explicit-remember]:", 1)[1].strip()
    # +1 for trailing ellipsis char.
    assert len(payload) <= append.MAX_OBSERVATION_CHARS + 1


# --------------------------------------------------------------------------- #
# Prompt builder
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_build_skills_context_injects_memory_blocks(
    fake_r2, db_session, workspace, user_a
):
    await seed.ensure_company_skill(workspace.id)
    await seed.ensure_team_skill(workspace.id)
    await seed.ensure_user_skill(workspace.id, user_a.id, user_a.slack_user_id)

    out = await prompt_builder.build_skills_context(
        user_a.id,
        workspace_id=workspace.id,
        slack_user_id=user_a.slack_user_id,
    )
    assert '<memory scope="company">' in out
    assert '<memory scope="team">' in out
    assert '<memory scope="user">' in out
    # Memory blocks render BEFORE any always_active_skills block (which is
    # absent here since the user has no other skills installed).
    assert "<always_active_skills>" not in out


@pytest.mark.asyncio
async def test_build_skills_context_skips_user_block_when_no_slack_id(
    fake_r2, db_session, workspace, user_a
):
    await seed.ensure_company_skill(workspace.id)
    await seed.ensure_team_skill(workspace.id)

    out = await prompt_builder.build_skills_context(
        user_a.id, workspace_id=workspace.id
    )
    assert '<memory scope="company">' in out
    assert '<memory scope="team">' in out
    assert '<memory scope="user">' not in out


@pytest.mark.asyncio
async def test_build_skills_context_no_workspace_id_no_memory(
    fake_r2, db_session, workspace, user_a
):
    """Legacy callers passing only user_id keep working: no memory blocks,
    no errors."""
    await seed.ensure_company_skill(workspace.id)
    out = await prompt_builder.build_skills_context(user_a.id)
    assert "<memory" not in out


# --------------------------------------------------------------------------- #
# Remember tool
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_remember_tool_user_scope(fake_r2, db_session, workspace, user_a):
    """Calling `remember` with scope='user' writes to the per-user memory
    and creates the stub on demand."""
    tool = get_tool("remember")
    assert tool is not None

    workspace_id_var.set(str(workspace.id))
    app_user_id_var.set(str(user_a.id))
    try:
        result = await tool.handler(scope="user", fact="Sam habla español")
    finally:
        workspace_id_var.set("")
        app_user_id_var.set("")
    assert "tu memoria personal" in result

    from app.skills import storage as skill_storage

    async with get_session() as session:
        skill = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace.id,
                    Skill.name == user_slug(user_a.slack_user_id),
                )
            )
        ).scalar_one()
    body = await skill_storage.download_skill_body(
        workspace_id=skill.workspace_id,
        skill_id=skill.id,
        version=skill.version,
        r2_ref=skill.body_r2_ref,
    )
    assert "Sam habla español" in body


@pytest.mark.asyncio
async def test_remember_tool_company_scope(fake_r2, db_session, workspace, user_a):
    tool = get_tool("remember")
    workspace_id_var.set(str(workspace.id))
    app_user_id_var.set(str(user_a.id))
    try:
        result = await tool.handler(scope="company", fact="Antiff es B2B")
    finally:
        workspace_id_var.set("")
        app_user_id_var.set("")
    assert "memoria de la empresa" in result

    from app.skills import storage as skill_storage

    async with get_session() as session:
        skill = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace.id,
                    Skill.name == COMPANY_SLUG,
                )
            )
        ).scalar_one()
    body = await skill_storage.download_skill_body(
        workspace_id=skill.workspace_id,
        skill_id=skill.id,
        version=skill.version,
        r2_ref=skill.body_r2_ref,
    )
    assert "Antiff es B2B" in body


@pytest.mark.asyncio
async def test_remember_tool_invalid_scope():
    tool = get_tool("remember")
    workspace_id_var.set(str(uuid.uuid4()))
    app_user_id_var.set(str(uuid.uuid4()))
    try:
        result = await tool.handler(scope="universe", fact="x")
    finally:
        workspace_id_var.set("")
        app_user_id_var.set("")
    assert "Scope inválido" in result


@pytest.mark.asyncio
async def test_remember_tool_empty_fact_rejected():
    tool = get_tool("remember")
    workspace_id_var.set(str(uuid.uuid4()))
    app_user_id_var.set(str(uuid.uuid4()))
    try:
        result = await tool.handler(scope="company", fact="   ")
    finally:
        workspace_id_var.set("")
        app_user_id_var.set("")
    assert "vacío" in result
