"""Integration tests for the follow-up system.

Covers:
  - `create_follow_up` validates input + dedupes by run id.
  - `fetch_due_pending` only returns pending rows scheduled at-or-before now.
  - `user_replied_since` correctly detects a user reply post-creation.
  - Worker `_fire_one` cancels when user replied, dispatches otherwise
    (mocking `run_agent` so we don't actually call Slack/LLM).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.db.models import FollowUp, Message, Thread
from app.db.session import get_session
from app.follow_ups import repository as repo
from app.follow_ups import worker


pytestmark = pytest.mark.integration


async def _seed_thread_with_assistant_msg(
    session, workspace_id, channel, conversation_key
):
    """Create a thread and one assistant message (so a user reply later
    is detectable). Returns the thread id."""
    thread = Thread(
        workspace_id=workspace_id,
        slack_channel_id=channel,
        slack_thread_ts=conversation_key,
    )
    session.add(thread)
    await session.flush()
    asst = Message(
        thread_id=thread.id,
        role="assistant",
        text="¿Me pasás el spec del Q3 para terminar el reporte?",
        app_user_id=None,
        slack_ts="1234567890.000001",
    )
    session.add(asst)
    await session.flush()
    return thread.id


@pytest.mark.asyncio
async def test_create_follow_up_validates_reason(fake_r2, db_session, workspace, user_a):
    new_id = await repo.create_follow_up(
        workspace_id=workspace.id,
        app_user_id=user_a.id,
        channel="C123",
        conversation_key="1234.5678",
        reply_thread_ts=None,
        reason="   ",  # whitespace
        wait_hours=24,
    )
    assert new_id is None


@pytest.mark.asyncio
async def test_create_follow_up_validates_wait_hours(
    fake_r2, db_session, workspace, user_a
):
    assert await repo.create_follow_up(
        workspace_id=workspace.id, app_user_id=user_a.id,
        channel="C", conversation_key="K", reply_thread_ts=None,
        reason="ok", wait_hours=0,
    ) is None
    assert await repo.create_follow_up(
        workspace_id=workspace.id, app_user_id=user_a.id,
        channel="C", conversation_key="K", reply_thread_ts=None,
        reason="ok", wait_hours=10_000,
    ) is None


@pytest.mark.asyncio
async def test_create_follow_up_happy_path(fake_r2, db_session, workspace, user_a):
    new_id = await repo.create_follow_up(
        workspace_id=workspace.id,
        app_user_id=user_a.id,
        channel="C123",
        conversation_key="1234.5678",
        reply_thread_ts="1234.5678",
        reason="spec Q3 para reporte",
        wait_hours=24,
    )
    assert new_id is not None

    async with get_session() as session:
        row = (
            await session.execute(select(FollowUp).where(FollowUp.id == new_id))
        ).scalar_one()
    assert row.status == "pending"
    assert row.nudge_count == 0
    assert row.reason == "spec Q3 para reporte"
    assert row.scheduled_for > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_create_follow_up_dedupes_by_run_id(fake_r2, db_session, workspace, user_a):
    """Same run shouldn't create two pending follow-ups."""
    run_id = "run-xyz"
    first = await repo.create_follow_up(
        workspace_id=workspace.id, app_user_id=user_a.id,
        channel="C", conversation_key="K", reply_thread_ts=None,
        reason="first", wait_hours=24, created_by_run_id=run_id,
    )
    second = await repo.create_follow_up(
        workspace_id=workspace.id, app_user_id=user_a.id,
        channel="C", conversation_key="K", reply_thread_ts=None,
        reason="second", wait_hours=24, created_by_run_id=run_id,
    )
    assert first == second  # dedup returned existing id

    async with get_session() as session:
        rows = (
            await session.execute(
                select(FollowUp.id).where(FollowUp.created_by_run_id == run_id)
            )
        ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_fetch_due_pending_returns_only_due(fake_r2, db_session, workspace, user_a):
    # One due (scheduled in the past via direct insert)
    async with get_session() as session:
        past = FollowUp(
            workspace_id=workspace.id, app_user_id=user_a.id,
            channel="C", conversation_key="K1", reason="due",
            scheduled_for=datetime.now(timezone.utc) - timedelta(hours=1),
            status="pending",
        )
        future = FollowUp(
            workspace_id=workspace.id, app_user_id=user_a.id,
            channel="C", conversation_key="K2", reason="not yet",
            scheduled_for=datetime.now(timezone.utc) + timedelta(hours=1),
            status="pending",
        )
        already_sent = FollowUp(
            workspace_id=workspace.id, app_user_id=user_a.id,
            channel="C", conversation_key="K3", reason="done",
            scheduled_for=datetime.now(timezone.utc) - timedelta(hours=2),
            status="sent",
        )
        session.add_all([past, future, already_sent])
        await session.commit()

    due = await repo.fetch_due_pending()
    keys = {r.conversation_key for r in due}
    assert "K1" in keys
    assert "K2" not in keys
    assert "K3" not in keys


@pytest.mark.asyncio
async def test_user_replied_since_detects_post_creation_message(
    fake_r2, db_session, workspace, user_a
):
    """A user message AFTER the follow-up's created_at counts as a reply."""
    channel = "C123"
    conversation_key = "1234.5678"
    async with get_session() as session:
        thread_id = await _seed_thread_with_assistant_msg(
            session, workspace.id, channel, conversation_key
        )
        marker_time = datetime.now(timezone.utc) - timedelta(seconds=1)
        # Insert a USER message AFTER the marker.
        user_msg = Message(
            thread_id=thread_id,
            role="user",
            app_user_id=user_a.id,
            text="acá te paso el spec: ...",
            slack_ts="1234567891.000002",
        )
        session.add(user_msg)
        await session.commit()

    replied = await repo.user_replied_since(
        workspace_id=workspace.id,
        app_user_id=user_a.id,
        channel=channel,
        conversation_key=conversation_key,
        since=marker_time,
    )
    assert replied is True


@pytest.mark.asyncio
async def test_user_replied_since_negative_when_no_new_message(
    fake_r2, db_session, workspace, user_a
):
    channel = "C123"
    conversation_key = "1234.5678"
    async with get_session() as session:
        await _seed_thread_with_assistant_msg(
            session, workspace.id, channel, conversation_key
        )
        await session.commit()

    # Marker in the future -> nothing newer than the future.
    future_marker = datetime.now(timezone.utc) + timedelta(minutes=1)
    replied = await repo.user_replied_since(
        workspace_id=workspace.id,
        app_user_id=user_a.id,
        channel=channel,
        conversation_key=conversation_key,
        since=future_marker,
    )
    assert replied is False


@pytest.mark.asyncio
async def test_fire_one_cancels_when_user_replied(
    fake_r2, db_session, workspace, user_a, monkeypatch
):
    """If the user replied to the thread since fu.created_at, the
    worker cancels and does NOT dispatch an agent run."""
    channel = "C456"
    conversation_key = "9876.5432"

    # Create the follow-up first.
    fu_id = await repo.create_follow_up(
        workspace_id=workspace.id, app_user_id=user_a.id,
        channel=channel, conversation_key=conversation_key,
        reply_thread_ts=conversation_key,
        reason="need data", wait_hours=24,
    )
    assert fu_id is not None

    # Then add a user reply to the thread AFTER fu.created_at.
    async with get_session() as session:
        thread = Thread(
            workspace_id=workspace.id,
            slack_channel_id=channel,
            slack_thread_ts=conversation_key,
        )
        session.add(thread)
        await session.flush()
        session.add(Message(
            thread_id=thread.id,
            role="user",
            app_user_id=user_a.id,
            text="ya te respondí",
            slack_ts="9876.5433",
        ))
        await session.commit()

    # Spy on run_agent to confirm it's NOT called.
    dispatched = []
    async def _no_call(**kwargs):
        dispatched.append(kwargs)
    monkeypatch.setattr(worker, "run_agent", _no_call)
    # Stub bot-token lookup (function checks before bailing).
    async def _ok_token(_ws):
        return ("xoxb-test", "U_BOT")
    monkeypatch.setattr(worker, "get_bot_token_by_workspace", _ok_token)

    async with get_session() as session:
        fu = (await session.execute(select(FollowUp).where(FollowUp.id == fu_id))).scalar_one()
    await worker._fire_one(fu)

    assert dispatched == []  # no agent run

    async with get_session() as session:
        refreshed = (
            await session.execute(select(FollowUp).where(FollowUp.id == fu_id))
        ).scalar_one()
    assert refreshed.status == "cancelled"
    assert refreshed.cancelled_reason == "user_replied"


@pytest.mark.asyncio
async def test_fire_one_dispatches_when_no_reply(
    fake_r2, db_session, workspace, user_a, monkeypatch
):
    """Happy path: user did not reply, worker fires an agent run + marks sent."""
    channel = "C999"
    conversation_key = "5555.6666"
    fu_id = await repo.create_follow_up(
        workspace_id=workspace.id, app_user_id=user_a.id,
        channel=channel, conversation_key=conversation_key,
        reply_thread_ts=conversation_key,
        reason="spec Q3", wait_hours=24,
    )
    assert fu_id is not None

    dispatched = []
    async def _capture(**kwargs):
        dispatched.append(kwargs)
    monkeypatch.setattr(worker, "run_agent", _capture)
    async def _ok_token(_ws):
        return ("xoxb-test", "U_BOT")
    monkeypatch.setattr(worker, "get_bot_token_by_workspace", _ok_token)

    async with get_session() as session:
        fu = (await session.execute(select(FollowUp).where(FollowUp.id == fu_id))).scalar_one()
    await worker._fire_one(fu)

    assert len(dispatched) == 1
    call = dispatched[0]
    assert call["channel"] == channel
    assert call["conversation_key"] == conversation_key
    assert call["slack_user_id"] == worker.SYSTEM_ACTOR_SLACK_USER_ID
    assert "spec Q3" in call["user_text"]

    async with get_session() as session:
        refreshed = (
            await session.execute(select(FollowUp).where(FollowUp.id == fu_id))
        ).scalar_one()
    assert refreshed.status == "sent"
    assert refreshed.nudge_count == 1
    assert refreshed.sent_at is not None


@pytest.mark.asyncio
async def test_fire_one_no_bot_token_leaves_pending(
    fake_r2, db_session, workspace, user_a, monkeypatch
):
    fu_id = await repo.create_follow_up(
        workspace_id=workspace.id, app_user_id=user_a.id,
        channel="C", conversation_key="K", reply_thread_ts=None,
        reason="x", wait_hours=24,
    )
    async def _no_token(_ws):
        return None
    monkeypatch.setattr(worker, "get_bot_token_by_workspace", _no_token)

    async with get_session() as session:
        fu = (await session.execute(select(FollowUp).where(FollowUp.id == fu_id))).scalar_one()
    await worker._fire_one(fu)

    # Still pending so the next tick retries.
    async with get_session() as session:
        refreshed = (
            await session.execute(select(FollowUp).where(FollowUp.id == fu_id))
        ).scalar_one()
    assert refreshed.status == "pending"
