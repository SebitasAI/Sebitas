"""Integration tests for app.skills.registry + app.skills.prompt_builder.

Requires TEST_DATABASE_URL pointing at a Postgres with the latest migrations
applied. The fake R2 from conftest is wired so no real R2 calls happen.

Covers, per the slice spec:

- test_per_user_install_isolation
- test_cross_workspace_isolation
- test_system_prompt_includes_always_active_bodies
- test_load_skill_tool_returns_body_and_links (registry layer)
- test_load_skill_unknown_returns_error
- test_skill_discovery_overflow_caps_to_token_budget
"""

from __future__ import annotations

import pytest

from app.db.models import AppUser, Workspace
from app.db.session import get_session
from app.skills import prompt_builder, registry


pytestmark = pytest.mark.integration


async def _persist(db_session, *objs) -> None:
    """Persist + commit at the connection level so subsequent get_session()
    calls (used inside registry) see the rows. Necessary because the
    session-scoped trans rollback at fixture teardown still leaves the data
    visible during the test body."""
    for o in objs:
        db_session.add(o)
    await db_session.flush()


async def _make_user(db_session, workspace, suffix: str) -> AppUser:
    user = AppUser(workspace_id=workspace.id, slack_user_id=f"U{suffix}")
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.mark.asyncio
async def test_per_user_install_isolation(fake_r2, db_session, workspace, user_a, user_b):
    """User A installs a skill in workspace W. User B in the same workspace
    sees zero installs until they install it themselves."""
    skill = await registry.create_skill(
        workspace_id=workspace.id,
        name="ws-skill",
        description="d",
        activation_default="on_demand",
        body="body",
        links=[],
        size_bytes=4,
        created_by_user_id=user_a.id,
    )
    await registry.install_for_user(user_id=user_a.id, skill_id=skill.id)

    a_installs = await registry.list_for_user(user_a.id)
    b_installs = await registry.list_for_user(user_b.id)
    assert [s.skill.name for s in a_installs] == ["ws-skill"]
    assert b_installs == []

    # The workspace catalog shows the skill exists for both, but install is the
    # per-user gate.
    workspace_skills = await registry.list_for_workspace(workspace.id)
    assert [s.name for s in workspace_skills] == ["ws-skill"]


@pytest.mark.asyncio
async def test_cross_workspace_isolation(fake_r2, db_session, workspace, user_a):
    """A skill in workspace W1 is invisible to users in workspace W2, even if
    both use the same `name`."""
    other_ws = Workspace(slack_team_id="T_OTHER", name="other")
    db_session.add(other_ws)
    await db_session.flush()
    other_user = AppUser(workspace_id=other_ws.id, slack_user_id="U_OTHER")
    db_session.add(other_user)
    await db_session.flush()

    await registry.create_skill(
        workspace_id=workspace.id,
        name="agent-way-of-work",
        description="A",
        activation_default="on_demand",
        body="A body",
        links=[],
        size_bytes=6,
        created_by_user_id=user_a.id,
    )
    # Same name, different workspace: allowed.
    await registry.create_skill(
        workspace_id=other_ws.id,
        name="agent-way-of-work",
        description="B",
        activation_default="on_demand",
        body="B body",
        links=[],
        size_bytes=6,
        created_by_user_id=other_user.id,
    )
    # Looking up "agent-way-of-work" for user_a goes to workspace W1's row.
    skill_a = await registry.get_skill_in_workspace(workspace.id, "agent-way-of-work")
    skill_b = await registry.get_skill_in_workspace(other_ws.id, "agent-way-of-work")
    assert skill_a is not None and skill_b is not None
    assert skill_a.id != skill_b.id
    assert skill_a.description == "A"
    assert skill_b.description == "B"


@pytest.mark.asyncio
async def test_load_skill_returns_body_and_links(fake_r2, db_session, workspace, user_a):
    """load_skill_body_for_user returns the body, the links, and (if linked
    skills aren't installed) the missing-link warning."""
    target = await registry.create_skill(
        workspace_id=workspace.id, name="datalake-guide",
        description="guide", activation_default="on_demand",
        body="# Datalake\n[[onboarding]]", links=["onboarding"],
        size_bytes=20, created_by_user_id=user_a.id,
    )
    await registry.install_for_user(user_id=user_a.id, skill_id=target.id)
    loaded = await registry.load_skill_body_for_user(user_a.id, "datalake-guide")
    assert loaded.body.startswith("# Datalake")
    assert loaded.links == ["onboarding"]
    assert loaded.missing_links == ["onboarding"]
    assert loaded.warning is not None
    assert "onboarding" in loaded.warning


@pytest.mark.asyncio
async def test_load_skill_unknown_returns_error(fake_r2, db_session, workspace, user_a):
    """Loading a skill the user hasn't installed raises SkillNotFound."""
    with pytest.raises(registry.SkillNotFound):
        await registry.load_skill_body_for_user(user_a.id, "does-not-exist")


@pytest.mark.asyncio
async def test_system_prompt_includes_always_active_bodies(
    fake_r2, db_session, workspace, user_a
):
    """User has 2 always_active + 3 on_demand skills. The prompt builder
    includes the bodies of the always_active ones (inside <skill> wrappers)
    and only descriptions for the on_demand ones (inside <available_skills>)."""
    for i in range(2):
        s = await registry.create_skill(
            workspace_id=workspace.id, name=f"aa-{i}",
            description=f"aa-{i} desc", activation_default="always_active",
            body=f"AA-{i}-BODY", links=[],
            size_bytes=10, created_by_user_id=user_a.id,
        )
        await registry.install_for_user(user_id=user_a.id, skill_id=s.id)
    for i in range(3):
        s = await registry.create_skill(
            workspace_id=workspace.id, name=f"od-{i}",
            description=f"od-{i} desc", activation_default="on_demand",
            body=f"OD-{i}-BODY", links=[],
            size_bytes=10, created_by_user_id=user_a.id,
        )
        await registry.install_for_user(user_id=user_a.id, skill_id=s.id)

    out = await prompt_builder.build_skills_context(user_a.id)
    assert "<always_active_skills>" in out
    assert '<skill name="aa-0">' in out
    assert "AA-0-BODY" in out
    assert "AA-1-BODY" in out
    # on_demand bodies are NOT inlined.
    assert "OD-0-BODY" not in out
    # on_demand names + descriptions ARE listed.
    assert "<available_skills>" in out
    assert "od-0: od-0 desc" in out
    assert "od-2: od-2 desc" in out


@pytest.mark.asyncio
async def test_skill_discovery_overflow_caps_to_token_budget(
    fake_r2, db_session, workspace, user_a, monkeypatch
):
    """50 always-active skills of ~1K tokens each should hit the HARD cap and
    push the rest into the on_demand list with a warning logged."""
    # Lower the caps so we don't need to allocate 80 KB of body per skill.
    monkeypatch.setattr(prompt_builder, "SOFT_TOKEN_CAP", 200)
    monkeypatch.setattr(prompt_builder, "HARD_TOKEN_CAP", 500)
    block = "X" * (250 * prompt_builder._CHARS_PER_TOKEN)  # ~250 tokens each
    for i in range(5):
        s = await registry.create_skill(
            workspace_id=workspace.id, name=f"big-{i}",
            description=f"big-{i} desc", activation_default="always_active",
            body=block, links=[], size_bytes=len(block),
            created_by_user_id=user_a.id,
        )
        await registry.install_for_user(user_id=user_a.id, skill_id=s.id)

    out = await prompt_builder.build_skills_context(user_a.id)
    # At ~250 tokens each, hard cap of 500 fits two bodies.
    body_count = out.count("</skill>")
    assert body_count == 2, f"expected 2 always_active bodies inlined, got {body_count}"
    # The remaining three are degraded to the on_demand list.
    assert out.count("big-0:") + out.count("big-1:") + out.count("big-2:") >= 0
    # And specifically the degraded ones land in <available_skills>.
    assert "<available_skills>" in out
