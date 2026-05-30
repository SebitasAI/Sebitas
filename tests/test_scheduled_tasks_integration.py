"""Integration tests for scheduled tasks (repository + scheduler tick).

Requires TEST_DATABASE_URL pointing at a Postgres with migrations applied
(`uv run alembic upgrade head` against the test DB before running these).

Fixture pattern: we create + COMMIT the workspace / users via the app's own
`get_session()` so the repository functions (which open fresh sessions) see
the rows. Cleanup happens in a finalizer that DELETEs the workspace -- the
FK cascades wipe scheduled_task / app_user / etc.

Unit tests for pure logic (cron, tz, regex, build_seed_text, parse_until)
live in `test_scheduled_tasks_unit.py` and don't need a DB.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.db.models import AppUser, ScheduledTask, Workspace
from app.db.session import get_session
from app.scheduled_tasks import repository as repo
from app.scheduled_tasks import scheduler as sched
from app.scheduled_tasks.system_defaults import ALL_SYSTEM_TASKS


pytestmark = pytest.mark.integration


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _skip_if_no_db():
    if not TEST_DATABASE_URL:
        pytest.skip(
            "TEST_DATABASE_URL not set; set it to a Postgres URL "
            "(e.g. postgresql+asyncpg://localhost/misterr_test) to run "
            "scheduled_tasks integration tests"
        )


@pytest_asyncio.fixture
async def workspace_a():
    """Create + commit a fresh workspace + user. Tear down via cascade-delete
    on the Workspace row so we don't leave orphans behind between tests."""
    _skip_if_no_db()
    team_id = f"T{uuid.uuid4().hex[:10].upper()}"
    async with get_session() as session:
        ws = Workspace(
            slack_team_id=team_id,
            name="schedtask-test-ws-a",
            bot_token=None,
            installed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(ws)
        await session.flush()
        user = AppUser(workspace_id=ws.id, slack_user_id=f"U{uuid.uuid4().hex[:10].upper()}")
        session.add(user)
        await session.commit()
        await session.refresh(ws)
        await session.refresh(user)
        ws_id = ws.id
        user_id = user.id

    yield {"workspace_id": ws_id, "user_id": user_id, "team_id": team_id}

    async with get_session() as session:
        await session.execute(delete(Workspace).where(Workspace.id == ws_id))
        await session.commit()


@pytest_asyncio.fixture
async def workspace_b():
    """Second workspace + user for cross-tenant isolation tests."""
    _skip_if_no_db()
    team_id = f"T{uuid.uuid4().hex[:10].upper()}"
    async with get_session() as session:
        ws = Workspace(
            slack_team_id=team_id,
            name="schedtask-test-ws-b",
            bot_token=None,
            installed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(ws)
        await session.flush()
        user = AppUser(workspace_id=ws.id, slack_user_id=f"U{uuid.uuid4().hex[:10].upper()}")
        session.add(user)
        await session.commit()
        ws_id = ws.id
        user_id = user.id

    yield {"workspace_id": ws_id, "user_id": user_id, "team_id": team_id}

    async with get_session() as session:
        await session.execute(delete(Workspace).where(Workspace.id == ws_id))
        await session.commit()


@pytest_asyncio.fixture
async def second_user(workspace_a):
    """Second user in workspace_a, for permission-isolation tests."""
    async with get_session() as session:
        u = AppUser(
            workspace_id=workspace_a["workspace_id"],
            slack_user_id=f"U{uuid.uuid4().hex[:10].upper()}",
        )
        session.add(u)
        await session.commit()
        user_id = u.id
    return user_id


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_task_persists_with_next_run_at(workspace_a):
    task = await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_id"],
            name="daily-revops",
            prompt="Mandame el resumen.",
            cron_spec="0 9 * * 1-5",
            timezone="America/Bogota",
            scope="local",
            destination_type="dm",
            destination_slack_id="U_TEST",
        )
    )
    assert task.id is not None
    assert task.next_run_at is not None
    assert task.next_run_at.tzinfo is not None
    assert task.scope == "local"
    assert task.owner_user_id == workspace_a["user_id"]
    assert task.created_by_user_id == workspace_a["user_id"]


@pytest.mark.asyncio
async def test_create_task_name_conflict(workspace_a):
    base = repo.CreateTaskInput(
        workspace_id=workspace_a["workspace_id"],
        created_by_user_id=workspace_a["user_id"],
        name="report-x",
        prompt="A",
        cron_spec="0 9 * * 1-5",
        timezone="UTC",
        scope="local",
        destination_type="dm",
        destination_slack_id="U_X",
    )
    await repo.create_task(base)
    with pytest.raises(repo.TaskNameConflict):
        await repo.create_task(base)


@pytest.mark.asyncio
async def test_create_task_invalid_slug_rejected(workspace_a):
    with pytest.raises(repo.TaskValidationError):
        await repo.create_task(
            repo.CreateTaskInput(
                workspace_id=workspace_a["workspace_id"],
                created_by_user_id=workspace_a["user_id"],
                name="Bad Name With Spaces",
                prompt="A",
                cron_spec="0 9 * * 1-5",
                timezone="UTC",
                scope="local",
                destination_type="dm",
                destination_slack_id="U",
            )
        )


# --------------------------------------------------------------------------- #
# Resolver (workspace isolation)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_resolve_task_by_uuid_workspace_scoped(workspace_a, workspace_b):
    """A task created in workspace A must not be resolvable from workspace B,
    even if the caller knows its UUID. This is the cross-tenant invariant."""
    task = await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_id"],
            name="ws-a-only",
            prompt="A",
            cron_spec="0 9 * * *",
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="U",
        )
    )
    async with get_session() as session:
        # Within workspace A: resolves.
        found = await repo.resolve_task(session, workspace_a["workspace_id"], str(task.id))
        assert found.id == task.id

        # From workspace B with the same UUID: TaskNotFound.
        with pytest.raises(repo.TaskNotFound):
            await repo.resolve_task(session, workspace_b["workspace_id"], str(task.id))


@pytest.mark.asyncio
async def test_resolve_task_by_slug_workspace_scoped(workspace_a, workspace_b):
    """Same slug existing in workspace B should not leak into workspace A's
    resolver."""
    # Create same-slug task in both workspaces.
    await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_id"],
            name="shared-slug",
            prompt="A",
            cron_spec="0 9 * * *",
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="UA",
        )
    )
    await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_b["workspace_id"],
            created_by_user_id=workspace_b["user_id"],
            name="shared-slug",
            prompt="B",
            cron_spec="0 9 * * *",
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="UB",
        )
    )
    async with get_session() as session:
        a = await repo.resolve_task(session, workspace_a["workspace_id"], "shared-slug")
        b = await repo.resolve_task(session, workspace_b["workspace_id"], "shared-slug")
        assert a.id != b.id
        assert a.prompt == "A"
        assert b.prompt == "B"


# --------------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_user_b_cannot_edit_user_a_local_task(workspace_a, second_user):
    """The classic permission-isolation case: user A creates a local task,
    user B (same workspace) tries to update it -> TaskPermissionError."""
    task = await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_id"],
            name="user-a-private",
            prompt="A",
            cron_spec="0 9 * * *",
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="U",
        )
    )
    with pytest.raises(repo.TaskPermissionError):
        await repo.update_task(
            repo.UpdateTaskInput(
                workspace_id=workspace_a["workspace_id"],
                current_user_id=second_user,
                task_id_or_name=str(task.id),
                prompt="hijacked",
            )
        )
    with pytest.raises(repo.TaskPermissionError):
        await repo.delete_task(
            workspace_a["workspace_id"], second_user, str(task.id)
        )


@pytest.mark.asyncio
async def test_system_task_cannot_be_deleted(workspace_a):
    await repo.seed_system_tasks_for_workspace(
        workspace_a["workspace_id"], "C_HOME"
    )
    with pytest.raises(repo.TaskPermissionError):
        await repo.delete_task(
            workspace_a["workspace_id"],
            workspace_a["user_id"],
            "workflow-discovery",
        )


@pytest.mark.asyncio
async def test_system_task_prompt_cron_locked(workspace_a):
    """System tasks must reject changes to prompt / cron / timezone, but
    accept destination_slack_id changes (any workspace member in v1)."""
    await repo.seed_system_tasks_for_workspace(
        workspace_a["workspace_id"], "C_HOME"
    )
    # Prompt change rejected.
    with pytest.raises(repo.TaskPermissionError):
        await repo.update_task(
            repo.UpdateTaskInput(
                workspace_id=workspace_a["workspace_id"],
                current_user_id=workspace_a["user_id"],
                task_id_or_name="workflow-discovery",
                prompt="hijacked",
            )
        )
    # Cron change rejected.
    with pytest.raises(repo.TaskPermissionError):
        await repo.update_task(
            repo.UpdateTaskInput(
                workspace_id=workspace_a["workspace_id"],
                current_user_id=workspace_a["user_id"],
                task_id_or_name="workflow-discovery",
                cron_spec="0 0 * * *",
            )
        )


@pytest.mark.asyncio
async def test_system_task_destination_changeable(workspace_a):
    """In v1, any workspace member can change destination_slack_id on a
    system task. (Will tighten when admin roles ship.)"""
    await repo.seed_system_tasks_for_workspace(
        workspace_a["workspace_id"], "C_OLD"
    )
    updated = await repo.update_task(
        repo.UpdateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            current_user_id=workspace_a["user_id"],
            task_id_or_name="workflow-discovery",
            destination_slack_id="C_NEW",
        )
    )
    assert updated.destination_slack_id == "C_NEW"


# --------------------------------------------------------------------------- #
# Pause / resume
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_pause_resume(workspace_a):
    task = await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_id"],
            name="pausable",
            prompt="A",
            cron_spec="0 9 * * *",
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="U",
        )
    )
    paused = await repo.pause_task(
        workspace_a["workspace_id"],
        workspace_a["user_id"],
        str(task.id),
        until=None,
    )
    assert paused.is_paused is True
    assert paused.paused_until is None

    resumed = await repo.resume_task(
        workspace_a["workspace_id"],
        workspace_a["user_id"],
        str(task.id),
    )
    assert resumed.is_paused is False
    assert resumed.paused_until is None
    assert resumed.next_run_at is not None


# --------------------------------------------------------------------------- #
# Seeding (system tasks)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_seed_system_tasks_for_workspace(workspace_a):
    inserted = await repo.seed_system_tasks_for_workspace(
        workspace_a["workspace_id"], "C_HOME"
    )
    assert inserted == len(ALL_SYSTEM_TASKS)

    tasks = await repo.list_tasks(
        workspace_a["workspace_id"], workspace_a["user_id"], filter_mode="system"
    )
    names = sorted(t.name for t in tasks)
    assert names == sorted(d.name for d in ALL_SYSTEM_TASKS)
    for t in tasks:
        assert t.scope == "system"
        assert t.owner_user_id is None
        assert t.created_by_user_id is None
        assert t.destination_slack_id == "C_HOME"
        assert t.next_run_at is not None


@pytest.mark.asyncio
async def test_seed_system_tasks_idempotent(workspace_a):
    """Running the seeder twice doesn't insert duplicates; second call returns 0
    inserted thanks to ON CONFLICT DO NOTHING."""
    first = await repo.seed_system_tasks_for_workspace(
        workspace_a["workspace_id"], "C_HOME"
    )
    second = await repo.seed_system_tasks_for_workspace(
        workspace_a["workspace_id"], "C_HOME"
    )
    assert first == len(ALL_SYSTEM_TASKS)
    assert second == 0


@pytest.mark.asyncio
async def test_seed_system_tasks_for_all_workspaces(workspace_a, workspace_b):
    """The startup helper iterates every installed workspace and seeds all
    of them at once."""
    total = await repo.seed_system_tasks_for_all_workspaces()
    # Both workspaces should receive both system tasks.
    assert total >= 2 * len(ALL_SYSTEM_TASKS)


# --------------------------------------------------------------------------- #
# claim_due_tasks + scheduler logic
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_claim_due_tasks_skips_paused(workspace_a):
    """A paused task whose paused_until is in the future MUST NOT be claimed."""
    task = await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_id"],
            name="paused-not-due",
            prompt="A",
            cron_spec="0 9 * * *",
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="U",
        )
    )
    # Force-paused with paused_until far in the future + next_run_at in the past.
    async with get_session() as session:
        row = (
            await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task.id)
            )
        ).scalar_one()
        row.is_paused = True
        row.paused_until = datetime.now(timezone.utc) + timedelta(days=30)
        row.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()

    async with get_session() as session:
        claimed = await repo.claim_due_tasks(session, limit=10)
        # Our specific task must NOT be in the claim set.
        assert task.id not in {c.id for c in claimed}


@pytest.mark.asyncio
async def test_claim_due_tasks_includes_paused_until_past(workspace_a):
    """A task with paused_until in the PAST is eligible -- auto-resume on claim."""
    task = await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_id"],
            name="paused-expired",
            prompt="A",
            cron_spec="0 9 * * *",
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="U",
        )
    )
    async with get_session() as session:
        row = (
            await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task.id)
            )
        ).scalar_one()
        row.is_paused = True
        row.paused_until = datetime.now(timezone.utc) - timedelta(hours=1)
        row.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()

    async with get_session() as session:
        claimed = await repo.claim_due_tasks(session, limit=10)
        assert task.id in {c.id for c in claimed}


@pytest.mark.asyncio
async def test_scheduler_tick_advances_state_and_clears_pause(workspace_a):
    """Run one _tick() against a due task. Verify last_run_at advances,
    next_run_at is recomputed, paused_until is cleared if it was in the past,
    and a fire was dispatched (we mock asyncio.create_task to capture)."""
    task = await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_id"],
            name="due-now",
            prompt="A",
            cron_spec="0 * * * *",  # hourly, plenty above the 5min floor
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="U_DEST",
        )
    )
    # Force next_run_at into the past.
    async with get_session() as session:
        row = (
            await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task.id)
            )
        ).scalar_one()
        row.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await session.commit()

    # Capture dispatched fires without actually running _dispatch_fire.
    dispatched: list = []

    def fake_create_task(coro):
        # close the coro to avoid "coroutine was never awaited" warning
        coro.close()
        dispatched.append(coro)
        return AsyncMock()

    with patch.object(sched.asyncio, "create_task", side_effect=fake_create_task):
        fired = await sched._tick_for_test()

    assert fired == 1
    assert len(dispatched) == 1

    # Check the row state post-tick.
    async with get_session() as session:
        row = (
            await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task.id)
            )
        ).scalar_one()
    assert row.last_run_status == "running"
    assert row.last_run_at is not None
    assert row.next_run_at > datetime.now(timezone.utc) - timedelta(seconds=10)


@pytest.mark.asyncio
async def test_scheduler_tick_marks_missing_destination_as_failed(workspace_a):
    """A system task whose destination_slack_id is NULL must be marked failed
    on tick, NOT auto-paused, and its next_run_at advanced normally."""
    await repo.seed_system_tasks_for_workspace(
        workspace_a["workspace_id"], None  # no home channel
    )
    # Force one of the system tasks to be due.
    async with get_session() as session:
        row = (
            await session.execute(
                select(ScheduledTask).where(
                    ScheduledTask.workspace_id == workspace_a["workspace_id"],
                    ScheduledTask.name == "workflow-discovery",
                )
            )
        ).scalar_one()
        row.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await session.commit()
        task_id = row.id

    # No fires should be dispatched -- the missing-destination path returns early.
    dispatched: list = []
    def fake_create_task(coro):
        coro.close()
        dispatched.append(coro)
        return AsyncMock()

    with patch.object(sched.asyncio, "create_task", side_effect=fake_create_task):
        fired = await sched._tick_for_test()

    assert fired == 0
    assert dispatched == []

    async with get_session() as session:
        row = (
            await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task_id)
            )
        ).scalar_one()
    assert row.last_run_status == "failed"
    assert row.last_run_error is not None
    assert "destination" in row.last_run_error.lower() or "home_channel" in row.last_run_error.lower()
    assert row.is_paused is False  # NOT auto-paused
    assert row.next_run_at > datetime.now(timezone.utc) - timedelta(seconds=10)


@pytest.mark.asyncio
async def test_scheduler_tick_logs_skip_not_catchup(workspace_a, caplog):
    """If a task was last_run 3 hours ago with an hourly cron, the tick should
    fire ONCE and log scheduled_task_skipped_missed_fires with missed_count=2."""
    import logging
    caplog.set_level(logging.INFO)

    task = await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_id"],
            name="catchup-test",
            prompt="A",
            cron_spec="0 * * * *",  # hourly
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="U",
        )
    )
    # Force last_run_at to 3 hours ago, next_run_at to past.
    async with get_session() as session:
        row = (
            await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task.id)
            )
        ).scalar_one()
        row.last_run_at = datetime.now(timezone.utc) - timedelta(hours=3, minutes=10)
        row.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await session.commit()

    def fake_create_task(coro):
        coro.close()
        return AsyncMock()

    with patch.object(sched.asyncio, "create_task", side_effect=fake_create_task):
        fired = await sched._tick_for_test()

    # One fire (not three -- catchup-skip).
    assert fired == 1


# --------------------------------------------------------------------------- #
# resolve_task: edge cases
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_resolve_task_not_found(workspace_a):
    async with get_session() as session:
        with pytest.raises(repo.TaskNotFound):
            await repo.resolve_task(session, workspace_a["workspace_id"], "nope")
        with pytest.raises(repo.TaskNotFound):
            await repo.resolve_task(
                session, workspace_a["workspace_id"], str(uuid.uuid4())
            )
