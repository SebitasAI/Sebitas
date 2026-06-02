"""Tests for the per-channel `<memory scope="channel">` block in the
system prompt (slice T-X follow-up).

Verifies that build_skills_context:
  - Loads the channel skill when given a non-DM channel_id.
  - Skips the channel block when in a 1:1 DM (channel_id starts with D).
  - Skips the channel block when no skill exists for the channel.
"""

from __future__ import annotations

import pytest

from app.memory import seed
from app.memory.constants import channel_slug
from app.skills import prompt_builder


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_channel_block_loaded_when_in_channel(
    fake_r2, db_session, workspace, user_a
):
    await seed.ensure_company_skill(workspace.id)
    await seed.ensure_team_skill(workspace.id)
    await seed.ensure_user_skill(workspace.id, user_a.id, user_a.slack_user_id)
    await seed.ensure_channel_skill(workspace.id, "C_ENG")

    out = await prompt_builder.build_skills_context(
        user_a.id,
        workspace_id=workspace.id,
        slack_user_id=user_a.slack_user_id,
        slack_channel_id="C_ENG",
    )
    assert '<memory scope="company">' in out
    assert '<memory scope="team">' in out
    assert '<memory scope="user">' in out
    assert '<memory scope="channel">' in out


@pytest.mark.asyncio
async def test_channel_block_skipped_in_1to1_dm(
    fake_r2, db_session, workspace, user_a
):
    """1:1 DMs use `users/<id>` instead of a channel skill. Even if the
    user has a channel skill seeded under that DM ID (which shouldn't
    happen but we test the prompt-builder behavior independently), the
    block must NOT appear because `is_one_to_one_dm` filters it out."""
    await seed.ensure_company_skill(workspace.id)
    await seed.ensure_team_skill(workspace.id)
    await seed.ensure_user_skill(workspace.id, user_a.id, user_a.slack_user_id)

    out = await prompt_builder.build_skills_context(
        user_a.id,
        workspace_id=workspace.id,
        slack_user_id=user_a.slack_user_id,
        slack_channel_id="D_PRIVATE_DM",
    )
    assert '<memory scope="user">' in out
    # No channel block in a 1:1 DM.
    assert '<memory scope="channel">' not in out


@pytest.mark.asyncio
async def test_channel_block_skipped_when_skill_missing(
    fake_r2, db_session, workspace, user_a
):
    """If a channel has never been scanned (no channels/<id> row),
    the prompt-builder just omits the block. The other memory blocks
    still load."""
    await seed.ensure_company_skill(workspace.id)
    await seed.ensure_team_skill(workspace.id)
    await seed.ensure_user_skill(workspace.id, user_a.id, user_a.slack_user_id)

    out = await prompt_builder.build_skills_context(
        user_a.id,
        workspace_id=workspace.id,
        slack_user_id=user_a.slack_user_id,
        slack_channel_id="C_NEVER_SCANNED",
    )
    assert '<memory scope="company">' in out
    assert '<memory scope="channel">' not in out


@pytest.mark.asyncio
async def test_ensure_channel_skill_refuses_1to1_dm(
    fake_r2, db_session, workspace
):
    """Even if called directly, ensure_channel_skill returns None for a
    1:1 DM channel ID (the 'D' prefix is the signal)."""
    result = await seed.ensure_channel_skill(workspace.id, "D_BAD")
    assert result is None
