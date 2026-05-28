"""Minimal, generic schema. Identity comes from Slack (team = workspace,
Slack user = app_user). No feature-specific tables yet."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
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
    # Per-tenant Slack install (slice multi-workspace). bot_token is Fernet-
    # encrypted at rest -- decrypt only via app.slack.crypto. Null until the
    # workspace is actually installed (e.g. a workspace row was created from
    # a message event before install completed; should be rare).
    bot_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    bot_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bot_scopes: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    installed_at: Mapped[datetime | None] = mapped_column(nullable=True)

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


# Postgres-side enums; reused in SkillInstall.activation_override so both sides
# agree on the discriminator values. `create_type=False` on the column types
# keeps Alembic from recreating the type on every metadata.create_all.
_SkillSourceEnum = Enum("upload", "catalog", name="skill_source", create_type=False)
_SkillActivationEnum = Enum(
    "always_active", "on_demand", name="skill_activation", create_type=False
)


class Skill(Base):
    """A skill: markdown body uploaded to a workspace. The body lives in R2 at
    `body_r2_ref`; this row holds the metadata used for discovery (description),
    discrimination (activation_default), tenancy (workspace_id), provenance
    (created_by_user_id, source), and cross-reference (links).

    The per-user install row (SkillInstall) sits on top: each user picks which
    workspace skills they install and may override activation per install."""

    __tablename__ = "skill"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_skill_workspace_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Slug, kebab-case, max ~40 chars in practice. Unique per workspace.
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # One-line, <= 280 chars in practice. Used in the discovery system prompt.
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # R2 key, NOT a URL. Looked up via app.skills.storage.download_skill_body.
    body_r2_ref: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    source: Mapped[str] = mapped_column(_SkillSourceEnum, nullable=False, default="upload")
    activation_default: Mapped[str] = mapped_column(
        _SkillActivationEnum, nullable=False, default="on_demand"
    )
    # Slugs extracted from `[[name]]` references in the body. Not foreign-key
    # constrained: a body may reference a sibling skill that doesn't exist yet.
    links: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SkillInstall(Base):
    """One workspace skill installed by one user. `activation_override` lets
    a user pin a skill as always-active even if its default is on_demand (or
    vice versa). (user_id, skill_id) UNIQUE prevents double installs."""

    __tablename__ = "skill_install"
    __table_args__ = (
        UniqueConstraint("user_id", "skill_id", name="uq_skill_install_user_skill"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skill.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Null = inherit `skill.activation_default`.
    activation_override: Mapped[str | None] = mapped_column(
        _SkillActivationEnum, nullable=True
    )
    installed_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )


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


class SlackUser(Base):
    """Workspace-scoped roster of Slack users. Cached locally so the agent can
    resolve `@name` -> `<@U...>` without hitting Slack on every message. Synced
    lazily on first use + periodically (12h) + incrementally on team_join /
    user_change / member_joined_channel events."""

    __tablename__ = "slack_user"
    __table_args__ = (UniqueConstraint("workspace_id", "slack_user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    real_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_synced_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )


class SlackChannel(Base):
    """Workspace-scoped cache of channel metadata + members. Members live in a
    JSONB list of slack_user_id strings (no join table -- channels rarely have
    millions of members; the JSONB list keeps reads cheap)."""

    __tablename__ = "slack_channel"
    __table_args__ = (UniqueConstraint("workspace_id", "slack_channel_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slack_channel_id: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    members: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )


class Space(TimestampMixin, Base):
    """A Space: live, read-only dashboard backed by the integrations gateway.

    Single template, parametrized by `data_binding` (what to query) and
    `access_list` (who can view). 4B-i uses an in-memory MockSpaceBackend;
    4B-ii will swap in a Convex shared-deployment backend behind the same
    SpaceBackend interface, no schema changes needed."""

    __tablename__ = "space"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Convex refs: nullable in single-deployment mode (only used when we move
    # to deployment-per-Space in 4B-iv). Keeping the columns to avoid a later
    # migration when that lands.
    convex_project_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    convex_deployment_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    frontend_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Reserved for future deployment-per-Space mode; unused in single-deployment.
    admin_key_vault_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data_binding: Mapped[dict] = mapped_column(JSONB, nullable=False)
    access_list: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_by_skill: Mapped[str | None] = mapped_column(String(128), nullable=True)


class SlackEventSeen(Base):
    """Dedupe table for Slack event delivery. Slack guarantees at-least-once;
    Bolt retries on non-2xx. We INSERT ON CONFLICT DO NOTHING and drop the
    event if the row already exists. Rows are TTL'd by a background cleanup."""

    __tablename__ = "slack_event_seen"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False, index=True
    )


class ThreadInbox(Base):
    """Queue of messages that arrived for a (team_id, conv_key) while another
    run was active on that thread. The holder of the per-thread mutex drains
    this and coalesces queued messages into a single follow-up turn."""

    __tablename__ = "thread_inbox"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[str] = mapped_column(String(32), nullable=False)
    conv_key: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )


class MessageAttachment(Base):
    """A file attached to a user message. The bytes live in R2 (`r2_ref`); this
    row keeps the reference so multi-turn re-attachment works without
    re-downloading from Slack (Slack file URLs are short-lived)."""

    __tablename__ = "message_attachment"
    __table_args__ = (UniqueConstraint("message_id", "slack_file_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("message.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slack_file_id: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    original_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(nullable=True)
    # Nullable since some attachment_types (youtube_link) don't have an R2 file.
    r2_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 'file' (the original case: image/pdf/text/audio/video in R2) or
    # 'youtube_link' (a URL detected in the message text; no R2 bytes).
    attachment_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="file", server_default="file"
    )
    # Free-form per-type info: youtube -> {video_id, title, channel, duration_s, url};
    # video -> {duration_s, extracted_audio_bytes}; etc.
    attachment_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    # For audio/video/youtube attachments: cached transcript so multi-turn
    # re-attach doesn't re-transcribe (cost + latency).
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
