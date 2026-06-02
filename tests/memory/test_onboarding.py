"""Tests for app.memory.onboarding (slice T-X Phase D).

Strategy: stub the Slack client + the haiku call. The function under test
is a coordinator that reads from local tables + Slack + LLM, writes to
memory skills. We verify each source independently (channels, members,
integrations, history) and the end-to-end summary shape.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import IntegrationConnection, Skill, SlackUser
from app.db.session import get_session
from app.memory import onboarding, seed
from app.memory.constants import COMPANY_SLUG, TEAM_SLUG


pytestmark = pytest.mark.integration


class _FakeSlackClient:
    """Minimal AsyncWebClient stand-in: returns canned responses for the
    two methods onboarding actually calls."""

    def __init__(self, *, channels=None, history=None):
        self._channels = channels or []
        # history: dict[channel_id, list[messages]]
        self._history = history or {}

    async def conversations_list(self, **_kwargs):
        return {"channels": self._channels, "response_metadata": {"next_cursor": ""}}

    async def conversations_history(self, *, channel, limit):
        msgs = self._history.get(channel, [])
        return {"messages": msgs[:limit]}


@pytest.mark.asyncio
async def test_scan_members_writes_one_observation_per_user(
    fake_r2, db_session, workspace, monkeypatch
):
    await seed.ensure_team_skill(workspace.id)

    # Seed a few SlackUsers including a bot and a deleted account (both
    # should be filtered out).
    db_session.add_all([
        SlackUser(
            workspace_id=workspace.id, slack_user_id="U_HUMAN1",
            display_name="alice", real_name="Alice Q", tz="America/Bogota",
            is_bot=False, deleted=False,
        ),
        SlackUser(
            workspace_id=workspace.id, slack_user_id="U_HUMAN2",
            display_name="bob", real_name="Bob Z", tz="Europe/Berlin",
            is_bot=False, deleted=False,
        ),
        SlackUser(
            workspace_id=workspace.id, slack_user_id="U_BOT",
            display_name="botbot", real_name="botbot",
            is_bot=True, deleted=False,
        ),
        SlackUser(
            workspace_id=workspace.id, slack_user_id="U_DEAD",
            display_name="ghost", real_name="ghost",
            is_bot=False, deleted=True,
        ),
    ])
    await db_session.flush()

    summary = await onboarding._scan_members(workspace.id)
    assert summary["members_written"] == 2

    from app.skills import storage as skill_storage

    async with get_session() as session:
        skill = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace.id,
                    Skill.name == TEAM_SLUG,
                )
            )
        ).scalar_one()
    body = await skill_storage.download_skill_body(
        workspace_id=skill.workspace_id, skill_id=skill.id,
        version=skill.version, r2_ref=skill.body_r2_ref,
    )
    assert "<@U_HUMAN1>" in body
    assert "<@U_HUMAN2>" in body
    assert "U_BOT" not in body
    assert "U_DEAD" not in body
    assert "America/Bogota" in body


@pytest.mark.asyncio
async def test_scan_integrations_writes_one_summary_observation(
    fake_r2, db_session, workspace
):
    await seed.ensure_company_skill(workspace.id)
    db_session.add_all([
        IntegrationConnection(
            workspace_id=workspace.id, app="linear",
            provider="composio", status="connected", scope="team",
        ),
        IntegrationConnection(
            workspace_id=workspace.id, app="datadog",
            provider="composio", status="connected", scope="team",
        ),
        IntegrationConnection(
            workspace_id=workspace.id, app="pendinginstall",
            provider="pipedream", status="pending", scope="team",
        ),
    ])
    await db_session.flush()

    summary = await onboarding._scan_integrations(workspace.id)
    assert summary["integrations_written"] == 1

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
        workspace_id=skill.workspace_id, skill_id=skill.id,
        version=skill.version, r2_ref=skill.body_r2_ref,
    )
    assert "linear" in body
    assert "datadog" in body
    # status='pending' must be excluded.
    assert "pendinginstall" not in body


@pytest.mark.asyncio
async def test_scan_channels_sorts_by_member_count(fake_r2, db_session, workspace):
    await seed.ensure_team_skill(workspace.id)
    client = _FakeSlackClient(channels=[
        {
            "id": "C_SMALL", "name": "general", "num_members": 3,
            "purpose": {"value": "Misc chitchat"}, "topic": {"value": ""},
        },
        {
            "id": "C_BIG", "name": "engineering", "num_members": 42,
            "purpose": {"value": "Eng team channel"}, "topic": {"value": "ship it"},
        },
        {
            "id": "C_MID", "name": "product", "num_members": 12,
            "purpose": {"value": ""}, "topic": {"value": ""},
        },
    ])
    summary, channels = await onboarding._scan_channels(workspace.id, client)
    assert summary["channels_written"] == 3
    # Sorted desc by member count.
    assert [c["id"] for c in channels] == ["C_BIG", "C_MID", "C_SMALL"]

    from app.skills import storage as skill_storage

    async with get_session() as session:
        skill = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace.id,
                    Skill.name == TEAM_SLUG,
                )
            )
        ).scalar_one()
    body = await skill_storage.download_skill_body(
        workspace_id=skill.workspace_id, skill_id=skill.id,
        version=skill.version, r2_ref=skill.body_r2_ref,
    )
    assert "#engineering (42 miembros)" in body
    assert "Eng team channel" in body


@pytest.mark.asyncio
async def test_scan_historical_messages_extracts_and_writes(
    fake_r2, db_session, workspace, monkeypatch
):
    await seed.ensure_company_skill(workspace.id)

    channels = [
        {"id": "C_ENG", "name": "engineering", "num_members": 20,
         "purpose": {"value": "Eng channel"}},
        {"id": "C_PROD", "name": "product", "num_members": 10,
         "purpose": {"value": "Product channel"}},
    ]
    history = {
        "C_ENG": [{"user": "U1", "text": "We migrated to Postgres last week"}],
        "C_PROD": [{"user": "U2", "text": "MVP launches next quarter"}],
    }
    client = _FakeSlackClient(channels=channels, history=history)

    # Stub the LLM extraction to deterministic facts.
    async def _fake_extract(*, channel_id, channel_name, channel_purpose, messages):
        if channel_id == "C_ENG":
            return ["Stack incluye Postgres"]
        if channel_id == "C_PROD":
            return ["MVP del producto sale el próximo quarter"]
        return []

    monkeypatch.setattr(onboarding, "_extract_facts_for_channel", _fake_extract)

    summary = await onboarding._scan_historical_messages(
        workspace.id, client, channels
    )
    assert summary["channels_scanned"] == 2
    assert summary["facts_written"] == 2

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
        workspace_id=skill.workspace_id, skill_id=skill.id,
        version=skill.version, r2_ref=skill.body_r2_ref,
    )
    assert "[#engineering] Stack incluye Postgres" in body
    assert "[#product] MVP del producto sale el próximo quarter" in body


@pytest.mark.asyncio
async def test_extract_facts_parses_json_array(monkeypatch):
    """Parser tolerates code fences and falls back to [] on bad JSON."""
    captured_prompts = []

    class _Resp:
        def __init__(self, content):
            self.choices = [type("C", (), {"message": type("M", (), {"content": content})})]
            self.usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5})

    async def _fake_acompletion(model, messages, **kwargs):
        captured_prompts.append(messages[0]["content"])
        return _Resp('```json\n["fact A", "fact B"]\n```')

    monkeypatch.setattr(onboarding.litellm, "acompletion", _fake_acompletion)

    facts = await onboarding._extract_facts_for_channel(
        channel_id="C1", channel_name="general",
        channel_purpose="x", messages=[{"user": "U1", "text": "hello"}],
    )
    assert facts == ["fact A", "fact B"]


@pytest.mark.asyncio
async def test_extract_facts_returns_empty_on_unparseable(monkeypatch):
    class _Resp:
        def __init__(self, content):
            self.choices = [type("C", (), {"message": type("M", (), {"content": content})})]
            self.usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5})

    async def _fake_acompletion(model, messages, **kwargs):
        return _Resp("Aquí va el resultado: fact A, fact B")

    monkeypatch.setattr(onboarding.litellm, "acompletion", _fake_acompletion)

    facts = await onboarding._extract_facts_for_channel(
        channel_id="C1", channel_name="general",
        channel_purpose="x", messages=[{"user": "U1", "text": "hello"}],
    )
    assert facts == []


@pytest.mark.asyncio
async def test_extract_facts_skips_when_no_messages():
    """No messages -> no haiku call, returns []."""
    facts = await onboarding._extract_facts_for_channel(
        channel_id="C1", channel_name="general", channel_purpose="x", messages=[]
    )
    assert facts == []
