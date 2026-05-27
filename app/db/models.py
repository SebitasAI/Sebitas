"""Minimal, generic schema. Identity comes from Slack (team = workspace,
Slack user = app_user). No feature-specific tables yet."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspace"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slack_team_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    users: Mapped[list["AppUser"]] = relationship(back_populates="workspace")
    threads: Mapped[list["Thread"]] = relationship(back_populates="workspace")


class AppUser(TimestampMixin, Base):
    __tablename__ = "app_user"
    __table_args__ = (UniqueConstraint("workspace_id", "slack_user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False)

    workspace: Mapped["Workspace"] = relationship(back_populates="users")


class Thread(TimestampMixin, Base):
    __tablename__ = "thread"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slack_channel_id", "slack_thread_ts"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slack_channel_id: Mapped[str] = mapped_column(String(32), nullable=False)
    slack_thread_ts: Mapped[str] = mapped_column(String(32), nullable=False)

    workspace: Mapped["Workspace"] = relationship(back_populates="threads")
    messages: Mapped[list["Message"]] = relationship(back_populates="thread")


class Message(TimestampMixin, Base):
    __tablename__ = "message"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("thread.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Nullable: assistant (bot) messages have no Slack user.
    app_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # "user" | "assistant"
    text: Mapped[str] = mapped_column(Text, nullable=False)
    slack_ts: Mapped[str | None] = mapped_column(String(32), nullable=True)

    thread: Mapped["Thread"] = relationship(back_populates="messages")
    user: Mapped["AppUser | None"] = relationship()
