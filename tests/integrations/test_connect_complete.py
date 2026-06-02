"""Regression tests for `app.integrations.connect.complete`.

The bug we fixed: when a user connected an app from the webapp instead
of from Slack DM, the IntegrationConnection row was created with
`pending_run_id=None` (no agent run to resume). The old early-return
in `complete()` was keyed on `pending_run_id is None` as a dedupe
signal, so it returned without persisting `status='connected'` or the
account_id. Result: row stuck at status='pending' forever, despite
Pipedream having a fully-authorized account.

These tests cover the three paths through complete():
  1. Webapp-initiated row (no pending_run_id) -> must flip + persist.
  2. Slack-initiated row (pending_run_id set) -> must flip + persist
     + clear pending_run_id + pending_ctx.
  3. Already-connected row, same account -> must no-op (idempotency).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import IntegrationConnection
from app.db.session import get_session
from app.integrations import connect


pytestmark = pytest.mark.integration


async def _make_row(
    *,
    workspace_id: uuid.UUID,
    app: str = "gong",
    status: str = "pending",
    pending_run_id: str | None = None,
    pending_ctx: dict | None = None,
    pipedream_account_id: str | None = None,
) -> uuid.UUID:
    async with get_session() as session:
        row = IntegrationConnection(
            workspace_id=workspace_id,
            app=app,
            provider="pipedream",
            status=status,
            scope="team",
            pending_run_id=pending_run_id,
            pending_ctx=pending_ctx,
            pipedream_account_id=pipedream_account_id,
        )
        session.add(row)
        await session.commit()
        return row.id


@pytest.mark.asyncio
async def test_complete_webapp_path_no_pending_run_id(
    fake_r2, db_session, workspace, monkeypatch
):
    """Regression test. Row created by /api/integrations/connections
    has no pending_run_id; complete() must still persist."""
    # Block the run-resume + button-deactivate side effects (they need
    # Slack tokens + a paused LangGraph run; not part of this test).
    async def _no_resume(_ctx):
        raise AssertionError("resume_after_connect must not be called for webapp path")
    monkeypatch.setattr(
        "app.agent.runner.resume_after_connect", _no_resume
    )

    row_id = await _make_row(
        workspace_id=workspace.id,
        app="gong",
        status="pending",
        pending_run_id=None,
        pending_ctx=None,
    )

    await connect.complete(str(workspace.id), "gong", "apn_TEST_GONG")

    async with get_session() as session:
        row = (
            await session.execute(
                select(IntegrationConnection).where(IntegrationConnection.id == row_id)
            )
        ).scalar_one()
    assert row.status == "connected"
    assert row.pipedream_account_id == "apn_TEST_GONG"


@pytest.mark.asyncio
async def test_complete_slack_path_with_pending_run_id(
    fake_r2, db_session, workspace, monkeypatch
):
    """Row created by start_connect (Slack DM) has a pending_run_id.
    complete() should flip + clear + resume."""
    resumed = []

    async def _capture_resume(ctx):
        resumed.append(ctx)

    monkeypatch.setattr("app.agent.runner.resume_after_connect", _capture_resume)
    # Also skip Slack button deactivation (no real bot token in tests).
    async def _no_token(_ws_id):
        return None
    monkeypatch.setattr("app.slack.tokens.get_bot_token_by_workspace", _no_token)

    row_id = await _make_row(
        workspace_id=workspace.id,
        app="hubspot",
        status="pending",
        pending_run_id="run-abc",
        pending_ctx={"channel": "C1", "user_ts": "1.0", "_buttons": []},
    )

    await connect.complete(str(workspace.id), "hubspot", "apn_HUB")

    async with get_session() as session:
        row = (
            await session.execute(
                select(IntegrationConnection).where(IntegrationConnection.id == row_id)
            )
        ).scalar_one()
    assert row.status == "connected"
    assert row.pipedream_account_id == "apn_HUB"
    assert row.pending_run_id is None
    assert row.pending_ctx is None
    # The resume_after_connect side effect fired with the stripped ctx
    # (no underscore-prefixed UI bookkeeping keys).
    assert len(resumed) == 1
    assert "_buttons" not in resumed[0]


@pytest.mark.asyncio
async def test_complete_already_connected_same_account_noop(
    fake_r2, db_session, workspace, monkeypatch
):
    """Second call with the same account_id must NOT touch the row.
    Prevents wasted writes + duplicate run-resumes if both webhook
    and poll fire for the same connect."""
    row_id = await _make_row(
        workspace_id=workspace.id,
        app="linear",
        status="connected",
        pipedream_account_id="apn_LIN",
        pending_run_id=None,
        pending_ctx=None,
    )

    async def _fail_resume(_ctx):
        raise AssertionError("resume must not be called on idempotent re-entry")
    monkeypatch.setattr(
        "app.agent.runner.resume_after_connect", _fail_resume
    )

    # No exception; no side effects.
    await connect.complete(str(workspace.id), "linear", "apn_LIN")

    async with get_session() as session:
        row = (
            await session.execute(
                select(IntegrationConnection).where(IntegrationConnection.id == row_id)
            )
        ).scalar_one()
    assert row.status == "connected"
    assert row.pipedream_account_id == "apn_LIN"


@pytest.mark.asyncio
async def test_complete_row_missing_silent_return(
    fake_r2, db_session, workspace
):
    """Webhook fires for an app where we never created a row. Don't crash."""
    # No row created.
    await connect.complete(str(workspace.id), "ghostapp", "apn_GHOST")
    # If we got here, no exception. Nothing to assert beyond that.
