"""End-to-end-ish test for the Slack file_share skill flow.

We don't boot Slack itself; we call `handle_skill_file_upload` directly with
a stubbed Slack client + stubbed `_download_md`. The frontmatter LLM is
mocked. R2 is faked. The DB is real (TEST_DATABASE_URL).

Asserts that after a `.md` is dropped:
  - the bot posts an ephemeral preview block-kit,
  - a subsequent `skill_install_confirm` actually creates a skill row + an
    install row owned by the user.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.skills import registry
from app.slack import skill_commands


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_slack_file_upload_flow_end_to_end(
    fake_r2, db_session, workspace, user_a, patch_litellm, monkeypatch
):
    """Drop a .md, get a preview, click Install, end up with a skill +
    install row for the user, and an R2 object holding the body."""
    patch_litellm(
        reply_text=(
            '{"name": "team-onboarding", '
            '"description": "Reglas iniciales para nuevos miembros.", '
            '"activation": "always_active"}'
        )
    )

    # Patch the file downloader so we don't touch aiohttp.
    body = b"# Team onboarding\nbody here"
    monkeypatch.setattr(
        skill_commands, "_download_md", AsyncMock(return_value=body)
    )

    # Stub Slack client. chat_postEphemeral is an AsyncMock so we can read
    # what got posted.
    client = AsyncMock()
    client.token = "xoxb-fake"

    # Precursor "user typed /sebitas skill upload"
    skill_commands._set_pending(workspace.slack_team_id, user_a.slack_user_id)
    assert skill_commands.is_skill_upload_pending(
        workspace.slack_team_id, user_a.slack_user_id
    )

    await skill_commands.handle_skill_file_upload(
        client=client,
        team_id=workspace.slack_team_id,
        slack_user_id=user_a.slack_user_id,
        channel="D_FAKE",
        file_obj={
            "id": "F_FAKE",
            "name": "ONBOARDING.md",
            "filetype": "markdown",
            "size": len(body),
            "url_private_download": "https://slack.example/files/F_FAKE",
        },
        thread_ts=None,
    )
    client.chat_postEphemeral.assert_awaited_once()
    posted = client.chat_postEphemeral.call_args
    assert "Skill detectada" in posted.kwargs["text"] or any(
        "Skill detectada" in (b.get("text", {}).get("text", "") if isinstance(b, dict) else "")
        for b in posted.kwargs.get("blocks", [])
    )
    # Precursor was consumed.
    assert not skill_commands.is_skill_upload_pending(
        workspace.slack_team_id, user_a.slack_user_id
    )

    # Pull the preview_id out of the action_id we just posted.
    blocks = posted.kwargs["blocks"]
    actions_block = next(b for b in blocks if b.get("type") == "actions")
    confirm_action = next(
        e for e in actions_block["elements"]
        if e["action_id"].startswith("skill_install_confirm:")
    )
    preview_id = confirm_action["action_id"].split(":", 1)[1]
    assert preview_id in skill_commands._previews

    # Simulate the user pressing "Install": call the registry path directly.
    p = skill_commands._previews[preview_id]
    skill_id_str = await skill_commands._persist_install(p)
    assert skill_id_str

    # Assert the skill + install rows exist for this user.
    swi = await registry.get_skill_for_user(user_a.id, "team-onboarding")
    assert swi is not None
    assert swi.skill.description.startswith("Reglas iniciales")
    assert swi.effective_activation == "always_active"
    # R2 has the body.
    assert any(b"Team onboarding" in v for v in fake_r2.objects.values())
