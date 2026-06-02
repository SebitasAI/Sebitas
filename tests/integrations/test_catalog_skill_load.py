"""Regression test for load_skill of auto-generated `integrations/<app>` skills.

The bug: catalog skills are workspace-scope with `activation_default='on_demand'`,
no SkillInstall rows. The agent's `load_skill` tool used to ONLY look at
per-user installed skills, so `load_skill('integrations/gong')` returned
"no instalada" -- even though the skill row existed.

Fix: `_load_skill` falls back to `load_workspace_catalog_skill` when the
per-user lookup misses. Catalog skills loadable by ANY user in the
workspace.
"""

from __future__ import annotations

import uuid

import pytest

from app.agent.context import app_user_id_var, workspace_id_var
from app.skills import registry as skill_registry


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_load_workspace_catalog_skill_succeeds(
    fake_r2, db_session, workspace, user_a
):
    """A catalog skill exists for the workspace; the new helper loads
    its body without requiring a SkillInstall row."""
    body = "## Available actions\n- gong-list-calls\n## Usage notes\n"
    skill = await skill_registry.create_skill(
        workspace_id=workspace.id,
        name="integrations/gong",
        description="Auto-generated Gong catalog.",
        activation_default="on_demand",
        body=body,
        links=[],
        size_bytes=len(body.encode("utf-8")),
        created_by_user_id=None,
        source="catalog",
        scope="workspace",
    )
    loaded = await skill_registry.load_workspace_catalog_skill(
        workspace.id, "integrations/gong"
    )
    assert loaded.name == "integrations/gong"
    assert "gong-list-calls" in loaded.body
    assert loaded.missing_links == []


@pytest.mark.asyncio
async def test_load_workspace_catalog_skill_raises_when_not_catalog(
    fake_r2, db_session, workspace, user_a
):
    """A non-catalog skill (regular upload) should NOT be loadable via
    this helper, even if the name matches. Protects against the
    fallback accidentally exposing private upload skills."""
    body = "private content"
    await skill_registry.create_skill(
        workspace_id=workspace.id,
        name="some-private-skill",
        description="A regular uploaded skill.",
        activation_default="on_demand",
        body=body,
        links=[],
        size_bytes=len(body.encode("utf-8")),
        created_by_user_id=user_a.id,
        source="upload",
        scope="workspace",
    )
    with pytest.raises(skill_registry.SkillNotFound):
        await skill_registry.load_workspace_catalog_skill(
            workspace.id, "some-private-skill"
        )


@pytest.mark.asyncio
async def test_agent_load_skill_falls_back_to_catalog(
    fake_r2, db_session, workspace, user_a
):
    """End-to-end: the agent tool `_load_skill` should succeed for a
    catalog skill that has no SkillInstall row."""
    from app.agent.tools import _load_skill

    body = "## Available actions\n- gong-get-extensive-data\n## Usage notes\n"
    await skill_registry.create_skill(
        workspace_id=workspace.id,
        name="integrations/gong",
        description="Auto-generated.",
        activation_default="on_demand",
        body=body,
        links=[],
        size_bytes=len(body.encode("utf-8")),
        created_by_user_id=None,
        source="catalog",
        scope="workspace",
    )

    # Set the contextvars the way the runner would.
    workspace_id_var.set(str(workspace.id))
    app_user_id_var.set(str(user_a.id))
    try:
        result = await _load_skill("integrations/gong")
    finally:
        workspace_id_var.set("")
        app_user_id_var.set("")

    assert "<skill name=\"integrations/gong\">" in result
    assert "gong-get-extensive-data" in result
    assert "no instalada" not in result
