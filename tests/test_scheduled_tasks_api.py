"""Integration tests for /api/scheduled-tasks REST endpoints (slice T-2).

Same DB requirements as `test_scheduled_tasks_integration.py`: needs
TEST_DATABASE_URL pointing at a Postgres with migrations applied.

Pattern: we override the FastAPI `require_app_user` Depends so tests don't
need a real Clerk JWT, and exercise the routes via httpx.AsyncClient with
ASGITransport. One test exercises the unauthenticated path (no override)
to confirm a missing Authorization header lands on 401.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.auth.clerk import ResolvedAppUser, require_app_user
from app.db.models import AppUser, ScheduledTask, Workspace
from app.db.session import get_session
from app.main import app
from app.scheduled_tasks import repository as repo


pytestmark = pytest.mark.integration


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _skip_if_no_db():
    if not TEST_DATABASE_URL:
        pytest.skip(
            "TEST_DATABASE_URL not set; needed for /api/scheduled-tasks tests"
        )


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """httpx AsyncClient pinned to the FastAPI app via ASGITransport. We
    skip the lifespan because none of the tested routes need the scheduler
    loop running."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def workspace_a():
    _skip_if_no_db()
    async with get_session() as session:
        ws = Workspace(
            slack_team_id=f"T{uuid.uuid4().hex[:10].upper()}",
            name="api-test-ws-a",
            installed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(ws)
        await session.flush()
        user_a = AppUser(workspace_id=ws.id, slack_user_id=f"U{uuid.uuid4().hex[:10].upper()}")
        user_b = AppUser(workspace_id=ws.id, slack_user_id=f"U{uuid.uuid4().hex[:10].upper()}")
        session.add(user_a)
        session.add(user_b)
        await session.commit()
        ws_id = ws.id
        user_a_id = user_a.id
        user_b_id = user_b.id

    yield {"workspace_id": ws_id, "user_a_id": user_a_id, "user_b_id": user_b_id}

    async with get_session() as session:
        await session.execute(delete(Workspace).where(Workspace.id == ws_id))
        await session.commit()


@pytest_asyncio.fixture
async def workspace_b():
    _skip_if_no_db()
    async with get_session() as session:
        ws = Workspace(
            slack_team_id=f"T{uuid.uuid4().hex[:10].upper()}",
            name="api-test-ws-b",
            installed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(ws)
        await session.flush()
        user = AppUser(
            workspace_id=ws.id, slack_user_id=f"U{uuid.uuid4().hex[:10].upper()}"
        )
        session.add(user)
        await session.commit()
        ws_id = ws.id
        user_id = user.id

    yield {"workspace_id": ws_id, "user_id": user_id}

    async with get_session() as session:
        await session.execute(delete(Workspace).where(Workspace.id == ws_id))
        await session.commit()


def _override_user_as(workspace_id, app_user_id, email="test@example.com"):
    """Helper: build a Depends-override that returns the given resolved user."""
    async def _override() -> ResolvedAppUser:
        return ResolvedAppUser(
            workspace_id=workspace_id,
            app_user_id=app_user_id,
            clerk_user_id="user_test",
            email=email,
        )
    return _override


@pytest_asyncio.fixture
async def auth_as_user_a(workspace_a):
    """Override the JWT auth Depends so the API sees user A. Cleanup restores
    the original dependency on teardown so concurrent test runs don't bleed."""
    app.dependency_overrides[require_app_user] = _override_user_as(
        workspace_a["workspace_id"], workspace_a["user_a_id"]
    )
    yield
    app.dependency_overrides.pop(require_app_user, None)


@pytest_asyncio.fixture
async def auth_as_user_b(workspace_a):
    app.dependency_overrides[require_app_user] = _override_user_as(
        workspace_a["workspace_id"], workspace_a["user_b_id"]
    )
    yield
    app.dependency_overrides.pop(require_app_user, None)


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_requires_auth(client):
    """No Authorization header -> 401."""
    _skip_if_no_db()
    res = await client.get("/api/scheduled-tasks")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_list_with_bad_bearer_returns_401(client):
    _skip_if_no_db()
    res = await client.get(
        "/api/scheduled-tasks", headers={"Authorization": "NotBearer foo"}
    )
    assert res.status_code == 401


# --------------------------------------------------------------------------- #
# List
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_mine_returns_only_owned_local_tasks(
    client, workspace_a, auth_as_user_a
):
    # User A creates one; user B creates one; system tasks seeded.
    await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_a_id"],
            name="a-task",
            prompt="A",
            cron_spec="0 9 * * *",
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="U_A",
        )
    )
    await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_b_id"],
            name="b-task",
            prompt="B",
            cron_spec="0 9 * * *",
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="U_B",
        )
    )
    await repo.seed_system_tasks_for_workspace(workspace_a["workspace_id"], "C_HOME")

    res = await client.get("/api/scheduled-tasks?filter=mine")
    assert res.status_code == 200, res.text
    data = res.json()
    names = {t["name"] for t in data["tasks"]}
    assert "a-task" in names
    assert "b-task" not in names
    assert "workflow-discovery" not in names  # system not in 'mine'
    assert data["total_count"] == len(data["tasks"])


@pytest.mark.asyncio
async def test_list_system_returns_only_system_tasks(
    client, workspace_a, auth_as_user_a
):
    await repo.seed_system_tasks_for_workspace(workspace_a["workspace_id"], "C_HOME")
    await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_a_id"],
            name="a-local",
            prompt="A",
            cron_spec="0 9 * * *",
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="U",
        )
    )

    res = await client.get("/api/scheduled-tasks?filter=system")
    assert res.status_code == 200
    data = res.json()
    scopes = {t["scope"] for t in data["tasks"]}
    assert scopes == {"system"}
    assert "workflow-discovery" in {t["name"] for t in data["tasks"]}


@pytest.mark.asyncio
async def test_list_all_includes_mine_and_system(
    client, workspace_a, auth_as_user_a
):
    await repo.seed_system_tasks_for_workspace(workspace_a["workspace_id"], "C_HOME")
    await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_a_id"],
            name="a-local",
            prompt="A",
            cron_spec="0 9 * * *",
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="U",
        )
    )

    res = await client.get("/api/scheduled-tasks?filter=all")
    assert res.status_code == 200
    data = res.json()
    names = {t["name"] for t in data["tasks"]}
    assert "a-local" in names
    assert "workflow-discovery" in names
    assert "daily-brief" in names


@pytest.mark.asyncio
async def test_list_cross_workspace_isolation(
    client, workspace_a, workspace_b
):
    """User in workspace A must not see tasks of workspace B."""
    await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_b["workspace_id"],
            created_by_user_id=workspace_b["user_id"],
            name="b-secret",
            prompt="B",
            cron_spec="0 9 * * *",
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="U",
        )
    )

    app.dependency_overrides[require_app_user] = _override_user_as(
        workspace_a["workspace_id"], workspace_a["user_a_id"]
    )
    try:
        res = await client.get("/api/scheduled-tasks?filter=all")
        assert res.status_code == 200
        names = {t["name"] for t in res.json()["tasks"]}
        assert "b-secret" not in names
    finally:
        app.dependency_overrides.pop(require_app_user, None)


# --------------------------------------------------------------------------- #
# Pause / resume
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_pause_without_until_means_indefinite(
    client, workspace_a, auth_as_user_a
):
    task = await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_a_id"],
            name="to-pause",
            prompt="A",
            cron_spec="0 9 * * *",
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="U",
        )
    )
    res = await client.post(f"/api/scheduled-tasks/{task.name}/pause", json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["is_paused"] is True
    assert body["paused_until"] is None


@pytest.mark.asyncio
async def test_pause_with_until_persists(client, workspace_a, auth_as_user_a):
    task = await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_a_id"],
            name="to-pause-until",
            prompt="A",
            cron_spec="0 9 * * *",
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="U",
        )
    )
    res = await client.post(
        f"/api/scheduled-tasks/{task.name}/pause",
        json={"until": "2027-01-15"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["is_paused"] is True
    assert body["paused_until"] is not None
    assert "2027-01-15" in body["paused_until"]


@pytest.mark.asyncio
async def test_pause_invalid_until_returns_400(
    client, workspace_a, auth_as_user_a
):
    task = await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_a_id"],
            name="bad-until",
            prompt="A",
            cron_spec="0 9 * * *",
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="U",
        )
    )
    res = await client.post(
        f"/api/scheduled-tasks/{task.name}/pause",
        json={"until": "tomorrow"},
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_pause_unknown_task_returns_404(
    client, workspace_a, auth_as_user_a
):
    res = await client.post(
        "/api/scheduled-tasks/does-not-exist/pause", json={}
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_user_b_cannot_pause_user_a_local_task(
    client, workspace_a, auth_as_user_b
):
    task = await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_a_id"],
            name="a-private",
            prompt="A",
            cron_spec="0 9 * * *",
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="U",
        )
    )
    res = await client.post(f"/api/scheduled-tasks/{task.name}/pause", json={})
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_pause_system_task_allowed_by_any_user(
    client, workspace_a, auth_as_user_b
):
    """System tasks: any workspace member can pause in v1."""
    await repo.seed_system_tasks_for_workspace(workspace_a["workspace_id"], "C_HOME")
    res = await client.post(
        "/api/scheduled-tasks/workflow-discovery/pause", json={}
    )
    assert res.status_code == 200, res.text
    assert res.json()["is_paused"] is True


@pytest.mark.asyncio
async def test_resume_clears_state(client, workspace_a, auth_as_user_a):
    task = await repo.create_task(
        repo.CreateTaskInput(
            workspace_id=workspace_a["workspace_id"],
            created_by_user_id=workspace_a["user_a_id"],
            name="to-resume",
            prompt="A",
            cron_spec="0 9 * * *",
            timezone="UTC",
            scope="local",
            destination_type="dm",
            destination_slack_id="U",
        )
    )
    await client.post(
        f"/api/scheduled-tasks/{task.name}/pause", json={"until": "2027-01-15"}
    )
    res = await client.post(f"/api/scheduled-tasks/{task.name}/resume")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["is_paused"] is False
    assert body["paused_until"] is None
    assert body["next_run_at"] is not None
