"""Tests for Phase 2 of follow-ups:

  - Escalation: after a nudge fires, the row is re-armed with status
    'pending' + bumped scheduled_for until nudge_count reaches MAX_NUDGES,
    then marked 'sent' (terminal).
  - Integration sweeper: stale pending IntegrationConnection rows
    produce follow-up rows; dedup by connection_id prevents duplicates.
  - Promise extraction: `_extract_promises` parses well-formed JSON,
    drops invalid wait_hours, caps at MAX_PROMISES_PER_TURN.
  - `extract_and_persist` with thread context schedules a follow-up
    when a promise is detected.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import FollowUp, IntegrationConnection
from app.db.session import get_session
from app.follow_ups import integration_sweeper, repository as fu_repo
from app.memory import post_pass


pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Escalation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_mark_nudge_fired_first_call_rearms(fake_r2, db_session, workspace, user_a):
    fu_id = await fu_repo.create_follow_up(
        workspace_id=workspace.id, app_user_id=user_a.id,
        channel="C", conversation_key="K", reply_thread_ts=None,
        reason="ok", wait_hours=24,
    )
    assert fu_id is not None

    async with get_session() as session:
        original = (await session.execute(
            select(FollowUp).where(FollowUp.id == fu_id)
        )).scalar_one()
    original_scheduled = original.scheduled_for

    rescheduled = await fu_repo.mark_nudge_fired_and_reschedule(fu_id)
    assert rescheduled is True

    async with get_session() as session:
        after = (await session.execute(
            select(FollowUp).where(FollowUp.id == fu_id)
        )).scalar_one()
    assert after.status == "pending"
    assert after.nudge_count == 1
    assert after.scheduled_for > original_scheduled


@pytest.mark.asyncio
async def test_mark_nudge_fired_terminal_at_max(fake_r2, db_session, workspace, user_a):
    """After MAX_NUDGES-1 escalations, the next call marks terminal."""
    fu_id = await fu_repo.create_follow_up(
        workspace_id=workspace.id, app_user_id=user_a.id,
        channel="C", conversation_key="K2", reply_thread_ts=None,
        reason="ok", wait_hours=24,
    )
    # Walk it up to the cap.
    for _ in range(fu_repo.MAX_NUDGES - 1):
        ok = await fu_repo.mark_nudge_fired_and_reschedule(fu_id)
        assert ok is True
    # The last call should mark sent (no more re-arming).
    last = await fu_repo.mark_nudge_fired_and_reschedule(fu_id)
    assert last is False

    async with get_session() as session:
        row = (await session.execute(
            select(FollowUp).where(FollowUp.id == fu_id)
        )).scalar_one()
    assert row.status == "sent"
    assert row.nudge_count == fu_repo.MAX_NUDGES
    assert row.sent_at is not None


# --------------------------------------------------------------------------- #
# Integration sweeper
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_integration_sweeper_skips_fresh(fake_r2, db_session, workspace, user_a):
    """A pending connection that's brand new should NOT trigger a follow-up."""
    async with get_session() as session:
        conn = IntegrationConnection(
            workspace_id=workspace.id,
            app="salesforce",
            provider="composio",
            status="pending",
            pending_run_id="run-x",
            pending_ctx={
                "channel": "C_TEST",
                "conversation_key": "1234.5",
                "reply_thread_ts": "1234.5",
                "slack_user_id": user_a.slack_user_id,
            },
            scope="team",
        )
        session.add(conn)
        await session.commit()

    counts = await integration_sweeper.sweep_once()
    assert counts["scheduled"] == 0


@pytest.mark.asyncio
async def test_integration_sweeper_schedules_stale(
    fake_r2, db_session, workspace, user_a
):
    """An old pending connection produces a follow-up. Idempotent on second run."""
    async with get_session() as session:
        conn = IntegrationConnection(
            workspace_id=workspace.id,
            app="hubspot",
            provider="composio",
            status="pending",
            pending_run_id="run-y",
            pending_ctx={
                "channel": "C_STALE",
                "conversation_key": "9999.1",
                "reply_thread_ts": "9999.1",
                "slack_user_id": user_a.slack_user_id,
            },
            scope="team",
            # Backdate creation past the staleness threshold.
            created_at=datetime.now(timezone.utc)
            - timedelta(hours=integration_sweeper.STALE_AFTER_HOURS + 1),
        )
        session.add(conn)
        await session.commit()

    counts = await integration_sweeper.sweep_once()
    assert counts["scheduled"] == 1

    # Run again -- dedup should kick in.
    counts2 = await integration_sweeper.sweep_once()
    assert counts2["scheduled"] == 0

    async with get_session() as session:
        rows = (await session.execute(
            select(FollowUp).where(FollowUp.workspace_id == workspace.id)
        )).scalars().all()
    assert len(rows) == 1
    assert "hubspot" in rows[0].reason


@pytest.mark.asyncio
async def test_integration_sweeper_skips_missing_ctx(fake_r2, db_session, workspace, user_a):
    async with get_session() as session:
        conn = IntegrationConnection(
            workspace_id=workspace.id,
            app="weirdapp",
            provider="composio",
            status="pending",
            pending_run_id="run-z",
            pending_ctx=None,  # no ctx -> skip
            scope="team",
            created_at=datetime.now(timezone.utc)
            - timedelta(hours=integration_sweeper.STALE_AFTER_HOURS + 1),
        )
        session.add(conn)
        await session.commit()

    counts = await integration_sweeper.sweep_once()
    assert counts["scheduled"] == 0


# --------------------------------------------------------------------------- #
# Promise extraction
# --------------------------------------------------------------------------- #


def _make_resp(content: str):
    class _Msg:
        def __init__(self, c): self.content = c
    class _Choice:
        def __init__(self, c): self.message = _Msg(c)
    class _Usage:
        prompt_tokens = 10
        completion_tokens = 5
    class _R:
        def __init__(self, c):
            self.choices = [_Choice(c)]
            self.usage = _Usage()
    return _R(content)


@pytest.mark.asyncio
async def test_extract_promises_parses_valid(monkeypatch):
    async def _fake(model, messages, **kwargs):
        return _make_resp('[{"action": "te mando el spec", "wait_hours": 24}]')
    monkeypatch.setattr(post_pass.litellm, "acompletion", _fake)

    out = await post_pass._extract_promises("te mando el spec mañana", "ok")
    assert out == [{"action": "te mando el spec", "wait_hours": 24}]


@pytest.mark.asyncio
async def test_extract_promises_drops_out_of_range_wait(monkeypatch):
    async def _fake(model, messages, **kwargs):
        return _make_resp(
            '[{"action": "x", "wait_hours": 1},'   # too short
            '{"action": "y", "wait_hours": 24}]'
        )
    monkeypatch.setattr(post_pass.litellm, "acompletion", _fake)
    out = await post_pass._extract_promises("user msg", "agent msg")
    # First dropped (wait_hours=1 < MIN), second kept; capped at MAX_PROMISES_PER_TURN=1
    assert len(out) == 1
    assert out[0]["action"] == "y"


@pytest.mark.asyncio
async def test_extract_promises_returns_empty_on_bad_json(monkeypatch):
    async def _fake(model, messages, **kwargs):
        return _make_resp("Aquí no hay JSON")
    monkeypatch.setattr(post_pass.litellm, "acompletion", _fake)
    assert await post_pass._extract_promises("a", "b") == []


@pytest.mark.asyncio
async def test_extract_and_persist_schedules_followup_from_promise(
    fake_r2, db_session, workspace, user_a, monkeypatch
):
    """End-to-end: post-pass detects a promise + creates a follow_up."""
    # Stub facts extraction to []
    async def _no_facts(user_text, agent_response):
        return []
    monkeypatch.setattr(post_pass, "_extract", _no_facts)

    # Stub promises extraction to return one
    async def _one_promise(user_text, agent_response):
        return [{"action": "le pregunto a Laura el lunes", "wait_hours": 72}]
    monkeypatch.setattr(post_pass, "_extract_promises", _one_promise)

    result = await post_pass.extract_and_persist(
        workspace_id=workspace.id,
        slack_user_id=user_a.slack_user_id,
        user_text="le pregunto a Laura el lunes y te aviso",
        agent_response="dale",
        app_user_id=user_a.id,
        channel="C_TEST",
        conversation_key="123.456",
        reply_thread_ts="123.456",
        run_id="run-promise-1",
    )
    assert result["promises_scheduled"] == 1

    async with get_session() as session:
        rows = (await session.execute(
            select(FollowUp).where(FollowUp.workspace_id == workspace.id)
        )).scalars().all()
    assert len(rows) == 1
    fu = rows[0]
    assert "le pregunto a Laura" in fu.reason
    assert "promesa" in fu.reason or "prometió" in fu.reason
    # wait_hours=72 -> scheduled ~3 days out
    delta = fu.scheduled_for - datetime.now(timezone.utc)
    assert timedelta(hours=70) < delta < timedelta(hours=74)


@pytest.mark.asyncio
async def test_extract_and_persist_skips_promises_without_thread_context(
    fake_r2, db_session, workspace, user_a, monkeypatch
):
    """Older callers (no channel/conversation_key) only get facts, no
    follow-up creation. Promise extractor is never invoked."""
    async def _no_facts(user_text, agent_response):
        return []
    monkeypatch.setattr(post_pass, "_extract", _no_facts)

    called = {"n": 0}
    async def _spy(user_text, agent_response):
        called["n"] += 1
        return []
    monkeypatch.setattr(post_pass, "_extract_promises", _spy)

    result = await post_pass.extract_and_persist(
        workspace_id=workspace.id,
        slack_user_id=user_a.slack_user_id,
        user_text="te mando algo",
        agent_response="dale",
        # NO app_user_id / channel / conversation_key
    )
    assert called["n"] == 0
    assert result["promises_extracted"] == 0
