"""Integration tests for web-login AppUser resolution.

Regression coverage for the bug where a user who signed up (Clerk) and is in
the workspace's Slack roster, but never DM'd the bot, got a 403 ("DM the bot
first") and was invisible in the workspace. Both the org-based path and the
legacy email-fallback path must now PROVISION the AppUser on the spot.

Requires TEST_DATABASE_URL (migrations applied). Cascade-delete teardown.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.auth.clerk import (
    ClerkClaims,
    _candidate_app_users_for_email,
    _resolve_via_org,
)
from app.db.models import AppUser, SlackUser, Workspace
from app.db.session import get_session

pytestmark = pytest.mark.integration

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _skip_if_no_db():
    if not TEST_DATABASE_URL:
        pytest.skip(
            "TEST_DATABASE_URL not set; set it to a Postgres URL to run "
            "auth resolution integration tests"
        )


def _claims(*, sub: str, email: str | None, org_id: str | None) -> ClerkClaims:
    return ClerkClaims(
        sub=sub, email=email, org_id=org_id, org_role=None, org_slug=None, raw={}
    )


async def _make_ws(*, clerk_org_id: str | None) -> uuid.UUID:
    team_id = f"T{uuid.uuid4().hex[:10].upper()}"
    async with get_session() as session:
        row = Workspace(
            slack_team_id=team_id, name="resolve-test-ws", clerk_org_id=clerk_org_id
        )
        session.add(row)
        await session.commit()
        return row.id


async def _add_slack_user(ws_id: uuid.UUID, slack_uid: str, email: str) -> None:
    async with get_session() as session:
        session.add(
            SlackUser(
                workspace_id=ws_id,
                slack_user_id=slack_uid,
                email=email,
                display_name="Tester",
            )
        )
        await session.commit()


async def _app_user_count(ws_id: uuid.UUID) -> int:
    async with get_session() as session:
        return len(
            (
                await session.execute(
                    select(AppUser).where(AppUser.workspace_id == ws_id)
                )
            ).scalars().all()
        )


@pytest_asyncio.fixture
async def cleanup_ws():
    _skip_if_no_db()
    created: list[uuid.UUID] = []
    yield created
    async with get_session() as session:
        for ws_id in created:
            await session.execute(delete(Workspace).where(Workspace.id == ws_id))
        await session.commit()


async def test_resolve_via_org_provisions_app_user(cleanup_ws):
    """Org path: signed-up user in the Slack roster but with no AppUser gets
    the row created + linked, instead of a 403."""
    org_id = f"org_{uuid.uuid4().hex[:12]}"
    ws_id = await _make_ws(clerk_org_id=org_id)
    cleanup_ws.append(ws_id)
    await _add_slack_user(ws_id, "UWEB1", "web1@ontop.test")

    assert await _app_user_count(ws_id) == 0

    resolved = await _resolve_via_org(
        _claims(sub="user_web1", email="WEB1@ontop.test", org_id=org_id)
    )

    assert resolved is not None
    assert resolved.workspace_id == ws_id
    assert resolved.clerk_user_id == "user_web1"
    async with get_session() as session:
        row = (
            await session.execute(
                select(AppUser).where(AppUser.id == resolved.app_user_id)
            )
        ).scalar_one()
    assert row.slack_user_id == "UWEB1"
    assert row.clerk_user_id == "user_web1"


async def test_resolve_via_org_403_when_not_in_roster(cleanup_ws):
    """Org path: a Clerk user in the org but NOT in the Slack roster still
    gets a 403 -- we can't invent a slack_user_id for them."""
    org_id = f"org_{uuid.uuid4().hex[:12]}"
    ws_id = await _make_ws(clerk_org_id=org_id)
    cleanup_ws.append(ws_id)

    with pytest.raises(HTTPException) as exc:
        await _resolve_via_org(
            _claims(sub="user_ghost", email="ghost@ontop.test", org_id=org_id)
        )
    assert exc.value.status_code == 403
    assert await _app_user_count(ws_id) == 0


async def test_resolve_via_org_links_existing_app_user(cleanup_ws):
    """Org path direct hit: an AppUser already linked by clerk_user_id is
    returned without creating a duplicate."""
    org_id = f"org_{uuid.uuid4().hex[:12]}"
    ws_id = await _make_ws(clerk_org_id=org_id)
    cleanup_ws.append(ws_id)
    async with get_session() as session:
        session.add(
            AppUser(
                workspace_id=ws_id,
                slack_user_id="UEXIST1",
                clerk_user_id="user_exist",
            )
        )
        await session.commit()

    resolved = await _resolve_via_org(
        _claims(sub="user_exist", email="exist@ontop.test", org_id=org_id)
    )
    assert resolved is not None
    assert await _app_user_count(ws_id) == 1


async def test_email_fallback_provisions_app_user(cleanup_ws):
    """Legacy email path (no org_id): roster match with no AppUser creates +
    links the membership row."""
    ws_id = await _make_ws(clerk_org_id=None)
    cleanup_ws.append(ws_id)
    email = f"fallback_{uuid.uuid4().hex[:6]}@ontop.test"
    await _add_slack_user(ws_id, "UFALL1", email)

    assert await _app_user_count(ws_id) == 0

    candidates = await _candidate_app_users_for_email(email, "user_fallback")

    assert len(candidates) == 1
    assert candidates[0].workspace_id == ws_id
    assert candidates[0].slack_user_id == "UFALL1"
    assert candidates[0].clerk_user_id == "user_fallback"
    assert await _app_user_count(ws_id) == 1
