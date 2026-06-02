"""Tests for the on-join channel deep-scan (slice T-X follow-up).

Covers:
- `_should_skip_deep_scan` heuristic
- `scan_single_channel` happy path + off-topic skip + history-failure skip
- The agent tool's `channel_id` branch
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.db.models import Skill
from app.db.session import get_session
from app.memory import onboarding, seed
from app.memory.constants import COMPANY_SLUG, channel_slug


pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Pure heuristic
# --------------------------------------------------------------------------- #


def test_should_skip_deep_scan_random():
    assert onboarding._should_skip_deep_scan("random") is True
    assert onboarding._should_skip_deep_scan("team-random") is True


def test_should_skip_deep_scan_social():
    assert onboarding._should_skip_deep_scan("social-club") is True
    assert onboarding._should_skip_deep_scan("off-topic-stuff") is True


def test_should_skip_deep_scan_work_channels_pass():
    assert onboarding._should_skip_deep_scan("engineering") is False
    assert onboarding._should_skip_deep_scan("product-planning") is False
    assert onboarding._should_skip_deep_scan("cliente-acme") is False


def test_should_skip_deep_scan_handles_none_and_empty():
    assert onboarding._should_skip_deep_scan(None) is False
    assert onboarding._should_skip_deep_scan("") is False


# --------------------------------------------------------------------------- #
# scan_single_channel
# --------------------------------------------------------------------------- #


class _FakeClient:
    def __init__(self, *, info=None, history=None, info_err=None, history_err=None):
        self._info = info
        self._history = history or {"messages": []}
        self._info_err = info_err
        self._history_err = history_err

    async def conversations_info(self, *, channel):
        if self._info_err:
            raise self._info_err
        return {"channel": self._info}

    async def conversations_history(self, *, channel, limit):
        if self._history_err:
            raise self._history_err
        return self._history


@pytest.mark.asyncio
async def test_scan_single_channel_happy_path(
    fake_r2, db_session, workspace, monkeypatch
):
    fake_client = _FakeClient(
        info={
            "name": "engineering",
            "purpose": {"value": "Eng team channel"},
        },
        history={
            "messages": [
                {"user": "U1", "text": "ya migramos a Postgres en prod"},
                {"user": "U2", "text": "perfecto, retiremos las MySQL dashboards"},
            ]
        },
    )

    async def _fake_token(workspace_id):
        return ("xoxb-test", "U_BOT")

    async def _fake_extract(**kwargs):
        return ["Stack incluye Postgres", "MySQL ya no se usa"]

    monkeypatch.setattr(onboarding, "get_bot_token_by_workspace", _fake_token)
    monkeypatch.setattr(onboarding, "AsyncWebClient", lambda token: fake_client)
    monkeypatch.setattr(onboarding, "_extract_facts_with_cap", _fake_extract)

    result = await onboarding.scan_single_channel(workspace.id, "C_ENG")
    assert result["facts_written"] == 2
    assert result["channel_name"] == "engineering"
    assert "skipped" not in result

    from app.skills import storage as skill_storage

    # The facts land in the per-channel skill (`channels/c_eng`), NOT in
    # the workspace `company` skill -- so responses in #engineering see
    # them but responses in #product don't.
    expected_slug = channel_slug("C_ENG")
    async with get_session() as session:
        skill = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace.id,
                    Skill.name == expected_slug,
                )
            )
        ).scalar_one()
    body = await skill_storage.download_skill_body(
        workspace_id=skill.workspace_id, skill_id=skill.id,
        version=skill.version, r2_ref=skill.body_r2_ref,
    )
    # No `[#channel]` prefix anymore: the WHOLE skill is the channel's
    # memory, so the channel context is implicit.
    assert "Stack incluye Postgres" in body
    assert "MySQL ya no se usa" in body
    # And specifically NOT in company.
    async with get_session() as session:
        company = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace.id,
                    Skill.name == COMPANY_SLUG,
                )
            )
        ).scalar_one_or_none()
    if company is not None:
        company_body = await skill_storage.download_skill_body(
            workspace_id=company.workspace_id, skill_id=company.id,
            version=company.version, r2_ref=company.body_r2_ref,
        )
        assert "Stack incluye Postgres" not in company_body


@pytest.mark.asyncio
async def test_scan_single_channel_skips_off_topic(
    fake_r2, db_session, workspace, monkeypatch
):
    fake_client = _FakeClient(
        info={"name": "random", "purpose": {"value": "chitchat"}},
    )

    async def _fake_token(workspace_id):
        return ("xoxb-test", "U_BOT")

    called = {"n": 0}

    async def _should_not_be_called(**kwargs):
        called["n"] += 1
        return []

    monkeypatch.setattr(onboarding, "get_bot_token_by_workspace", _fake_token)
    monkeypatch.setattr(onboarding, "AsyncWebClient", lambda token: fake_client)
    monkeypatch.setattr(onboarding, "_extract_facts_with_cap", _should_not_be_called)

    result = await onboarding.scan_single_channel(workspace.id, "C_RANDOM")
    assert result["skipped"] == "off_topic"
    assert result["facts_written"] == 0
    # Most importantly: the model was NOT called.
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_scan_single_channel_handles_not_in_channel(
    fake_r2, db_session, workspace, monkeypatch
):
    fake_client = _FakeClient(
        info={"name": "private-stuff", "purpose": {"value": ""}},
        history_err=RuntimeError("not_in_channel"),
    )

    async def _fake_token(workspace_id):
        return ("xoxb-test", "U_BOT")

    monkeypatch.setattr(onboarding, "get_bot_token_by_workspace", _fake_token)
    monkeypatch.setattr(onboarding, "AsyncWebClient", lambda token: fake_client)

    result = await onboarding.scan_single_channel(workspace.id, "C_PRIV")
    assert result["skipped"] == "history_failed"
    assert result["facts_written"] == 0


@pytest.mark.asyncio
async def test_scan_single_channel_no_bot_token(fake_r2, db_session, workspace, monkeypatch):
    async def _no_token(workspace_id):
        return None

    monkeypatch.setattr(onboarding, "get_bot_token_by_workspace", _no_token)
    result = await onboarding.scan_single_channel(workspace.id, "C_X")
    assert result["skipped"] == "no_token"


@pytest.mark.asyncio
async def test_scan_single_channel_skips_one_to_one_dm(
    fake_r2, db_session, workspace, monkeypatch
):
    """1:1 DMs reuse `users/<id>` -- there's no channel skill to write to.
    The scan short-circuits at the top without touching Slack OR the model."""
    api_called = {"info": 0, "history": 0}

    class _CountingClient:
        async def conversations_info(self, *, channel):
            api_called["info"] += 1
            return {"channel": {"name": "x"}}

        async def conversations_history(self, *, channel, limit):
            api_called["history"] += 1
            return {"messages": []}

    async def _fake_token(workspace_id):
        return ("xoxb-test", "U_BOT")

    monkeypatch.setattr(onboarding, "get_bot_token_by_workspace", _fake_token)
    monkeypatch.setattr(onboarding, "AsyncWebClient", lambda token: _CountingClient())

    # Channel IDs starting with 'D' are 1:1 DMs.
    result = await onboarding.scan_single_channel(workspace.id, "D_PRIVATE_DM")
    assert result["skipped"] == "one_to_one_dm"
    assert result["facts_written"] == 0
    # Most importantly: zero Slack API calls. We short-circuit before touching it.
    assert api_called == {"info": 0, "history": 0}


@pytest.mark.asyncio
async def test_scan_single_channel_empty_history(
    fake_r2, db_session, workspace, monkeypatch
):
    fake_client = _FakeClient(
        info={"name": "engineering", "purpose": {"value": ""}},
        history={"messages": []},
    )

    async def _fake_token(workspace_id):
        return ("xoxb-test", "U_BOT")

    monkeypatch.setattr(onboarding, "get_bot_token_by_workspace", _fake_token)
    monkeypatch.setattr(onboarding, "AsyncWebClient", lambda token: fake_client)

    result = await onboarding.scan_single_channel(workspace.id, "C_EMPTY")
    assert result["skipped"] == "empty"
    assert result["facts_written"] == 0
