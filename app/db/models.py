"""Minimal, generic schema. Identity comes from Slack (team = workspace,
Slack user = app_user). No feature-specific tables yet."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant | tool
    text: Mapped[str] = mapped_column(Text, nullable=False)
    slack_ts: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Agent turns: tool_use blocks on an assistant turn; tool_call_id on a tool result.
    tool_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    thread: Mapped["Thread"] = relationship(back_populates="messages")
    user: Mapped["AppUser | None"] = relationship()


class Skill(Base):
    """A skill is DATA: metadata in this row, package (SKILL.md + manifest +
    resources) in R2 at `manifest_ref`. Never hardcoded; entered via the registry."""

    __tablename__ = "skill"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class SkillInstall(Base):
    """A skill installed in a workspace. (workspace_id, skill_id) unique."""

    __tablename__ = "skill_install"
    __table_args__ = (UniqueConstraint("workspace_id", "skill_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skill.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    installed_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class IntegrationConnection(Base):
    """A Pipedream-connected app for a workspace. Stores ONLY the connected-account
    reference (pipedream_account_id); credentials live in Pipedream, never here."""

    __tablename__ = "integration_connection"
    __table_args__ = (UniqueConstraint("workspace_id", "app"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    app: Mapped[str] = mapped_column(String(64), nullable=False)
    # Empty until connected; "pending" while awaiting an in-conversation connect.
    pipedream_account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="connected")
    # In-conversation connect flow: the paused run to auto-resume once connected.
    pending_run_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    pending_ctx: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
