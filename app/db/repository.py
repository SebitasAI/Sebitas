"""Thin persistence helpers. No domain logic, just upserts + inserts."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser, Message, Thread, Workspace


async def upsert_workspace(
    session: AsyncSession, slack_team_id: str, name: str | None = None
) -> Workspace:
    result = await session.execute(
        select(Workspace).where(Workspace.slack_team_id == slack_team_id)
    )
    workspace = result.scalar_one_or_none()
    if workspace is None:
        workspace = Workspace(slack_team_id=slack_team_id, name=name)
        session.add(workspace)
        await session.flush()
    elif name and workspace.name != name:
        workspace.name = name
    return workspace


async def upsert_app_user(
    session: AsyncSession, workspace_id: uuid.UUID, slack_user_id: str
) -> AppUser:
    result = await session.execute(
        select(AppUser).where(
            AppUser.workspace_id == workspace_id,
            AppUser.slack_user_id == slack_user_id,
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = AppUser(workspace_id=workspace_id, slack_user_id=slack_user_id)
        session.add(user)
        await session.flush()
    return user


async def get_or_create_thread(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    slack_channel_id: str,
    slack_thread_ts: str,
) -> Thread:
    result = await session.execute(
        select(Thread).where(
            Thread.workspace_id == workspace_id,
            Thread.slack_channel_id == slack_channel_id,
            Thread.slack_thread_ts == slack_thread_ts,
        )
    )
    thread = result.scalar_one_or_none()
    if thread is None:
        thread = Thread(
            workspace_id=workspace_id,
            slack_channel_id=slack_channel_id,
            slack_thread_ts=slack_thread_ts,
        )
        session.add(thread)
        await session.flush()
    return thread


async def add_message(
    session: AsyncSession,
    thread_id: uuid.UUID,
    *,
    role: str,
    text: str,
    app_user_id: uuid.UUID | None = None,
    slack_ts: str | None = None,
) -> Message:
    message = Message(
        thread_id=thread_id,
        role=role,
        text=text,
        app_user_id=app_user_id,
        slack_ts=slack_ts,
    )
    session.add(message)
    await session.flush()
    return message


async def get_workspace(session: AsyncSession, slack_team_id: str) -> Workspace | None:
    result = await session.execute(
        select(Workspace).where(Workspace.slack_team_id == slack_team_id)
    )
    return result.scalar_one_or_none()


async def get_thread(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    slack_channel_id: str,
    slack_thread_ts: str,
) -> Thread | None:
    result = await session.execute(
        select(Thread).where(
            Thread.workspace_id == workspace_id,
            Thread.slack_channel_id == slack_channel_id,
            Thread.slack_thread_ts == slack_thread_ts,
        )
    )
    return result.scalar_one_or_none()


async def get_thread_messages(
    session: AsyncSession, thread_id: uuid.UUID, limit: int = 20
) -> list[Message]:
    """Most recent `limit` messages of a thread, in chronological order."""
    result = await session.execute(
        select(Message)
        .where(Message.thread_id == thread_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))
