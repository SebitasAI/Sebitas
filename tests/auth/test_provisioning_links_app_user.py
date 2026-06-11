"""Integration tests for AppUser provisioning during Clerk-org setup.

Regression coverage for the "0 users / locked out of the web app" bug:
`_link_app_user_clerk_id` used to be a no-op when no AppUser row existed,
so a freshly-installed workspace never gained a member until someone DM'd
the bot. It must now CREATE the membership row.

Requires TEST_DATABASE_URL pointing at a Postgres with migrations applied
(`uv run alembic upgrade head` against the test DB first). Same fixture
pattern as test_scheduled_tasks_integration: create+commit via the app's
own get_session(), tear down via cascade-delete on the Workspace row.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.auth.clerk_provisioning import _link_app_user_clerk_id
from app.db.models import AppUser, Workspace
from app.db.repository import upsert_app_user
from app.db.session import get_session

pytestmark = pytest.mark.integration

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _skip_if_no_db():
    if not TEST_DATABASE_URL:
        pytest.skip(
            "TEST_DATABASE_URL not set; set it to a Postgres URL to run "
            "auth provisioning integration tests"
        )


@pytest_asyncio.fixture
async def ws():
    _skip_if_no_db()
    team_id = f"T{uuid.uuid4().hex[:10].upper()}"
    async with get_session() as session:
        row = Workspace(slack_team_id=team_id, name="auth-test-ws")
        session.add(row)
        await session.commit()
        ws_id = row.id
    yield ws_id
    async with get_session() as session:
        await session.execute(delete(Workspace).where(Workspace.id == ws_id))
        await session.commit()


async def _get_app_user(ws_id, slack_user_id):
    async with get_session() as session:
        return (
            await session.execute(
                select(AppUser).where(
                    AppUser.workspace_id == ws_id,
                    AppUser.slack_user_id == slack_user_id,
                )
            )
        ).scalar_one_or_none()


async def test_link_creates_app_user_when_missing(ws):
    """The core regression: linking when no AppUser exists must CREATE it,
    not silently no-op."""
    slack_uid = "UINSTALLER1"
    clerk_uid = "user_clerk_installer"

    assert await _get_app_user(ws, slack_uid) is None

    await _link_app_user_clerk_id(ws, slack_uid, clerk_uid)

    row = await _get_app_user(ws, slack_uid)
    assert row is not None, "AppUser should have been created on link"
    assert row.clerk_user_id == clerk_uid


async def test_link_backfills_clerk_id_on_existing_row(ws):
    """An AppUser created earlier (e.g. by a Slack message) with no
    clerk_user_id gets the id backfilled on link."""
    slack_uid = "UEXISTING1"
    async with get_session() as session:
        await upsert_app_user(session, ws, slack_uid)
        await session.commit()

    await _link_app_user_clerk_id(ws, slack_uid, "user_clerk_backfill")

    row = await _get_app_user(ws, slack_uid)
    assert row is not None
    assert row.clerk_user_id == "user_clerk_backfill"


async def test_link_is_idempotent(ws):
    """Re-linking the same identity twice doesn't error or duplicate."""
    slack_uid = "UIDEMPOTENT1"
    clerk_uid = "user_clerk_idem"
    await _link_app_user_clerk_id(ws, slack_uid, clerk_uid)
    await _link_app_user_clerk_id(ws, slack_uid, clerk_uid)

    async with get_session() as session:
        rows = (
            await session.execute(
                select(AppUser).where(
                    AppUser.workspace_id == ws,
                    AppUser.slack_user_id == slack_uid,
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].clerk_user_id == clerk_uid


async def test_upsert_app_user_does_not_overwrite_existing_clerk_id(ws):
    """upsert_app_user backfills a null clerk_user_id but never re-points a
    row that already has a different one (that would be a caller bug)."""
    slack_uid = "UNOOVERWRITE1"
    async with get_session() as session:
        await upsert_app_user(session, ws, slack_uid, clerk_user_id="user_first")
        await session.commit()
    async with get_session() as session:
        user = await upsert_app_user(
            session, ws, slack_uid, clerk_user_id="user_second"
        )
        await session.commit()
        assert user.clerk_user_id == "user_first"
