"""Minimal, generic schema. Identity comes from Slack (team = workspace,
Slack user = app_user). No feature-specific tables yet."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
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
    # Channel where Misterr was installed. System tasks post here by default;
    # null until the installer picks (or admin sets it manually). Migration 0017.
    bot_home_channel_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Clerk Organization id (e.g. "org_2abc...") backing this Slack workspace's
    # team membership. 1:1 mapping enforced by UNIQUE. Created automatically
    # on first install (when the installer has a Clerk user); for legacy
    # workspaces installed before this slice, populated by the backfill
    # script `app/auth/migrations/backfill_clerk_orgs.py`.
    clerk_org_id: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)

    users: Mapped[list["AppUser"]] = relationship(back_populates="workspace")
    threads: Mapped[list["Thread"]] = relationship(back_populates="workspace")


class AppUser(TimestampMixin, Base):
    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slack_user_id"),
        # One Clerk user maps to exactly one AppUser per workspace. Cross
        # workspaces, the same Clerk user can have many AppUser rows (one per
        # workspace they belong to). Migration 0024.
        UniqueConstraint(
            "workspace_id", "clerk_user_id", name="uq_app_user_workspace_clerk_user"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # Clerk user id ("user_2..."). Populated by the backfill script for
    # legacy rows, and on first web login for new users. Once non-null, the
    # require_app_user Depends uses this instead of email matching.
    clerk_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)

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


# Allowed discriminator values, enforced at the DB layer via CheckConstraint
# and at the application layer by the registry's type annotations. We use
# VARCHAR + CHECK instead of Postgres ENUM because SQLAlchemy 2.x + asyncpg
# + Alembic interact badly on enum-type migrations (the `create_type=False`
# hint does not prevent op.create_table from issuing CREATE TYPE, causing
# collisions on replay after a partial failure).
_SKILL_SOURCES = ("upload", "catalog")
_SKILL_ACTIVATIONS = ("always_active", "on_demand")
_SKILL_SCOPES = ("workspace", "personal")


class Skill(Base):
    """A skill: markdown body uploaded to a workspace. The body lives in R2 at
    `body_r2_ref`; this row holds the metadata used for discovery (description),
    discrimination (activation_default), tenancy (workspace_id), provenance
    (created_by_user_id, source), and cross-reference (links).

    The per-user install row (SkillInstall) sits on top: each user picks which
    workspace skills they install and may override activation per install."""

    __tablename__ = "skill"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_skill_workspace_name"),
        CheckConstraint(
            "source IN ('upload', 'catalog')", name="ck_skill_source"
        ),
        CheckConstraint(
            "activation_default IN ('always_active', 'on_demand')",
            name="ck_skill_activation_default",
        ),
        CheckConstraint(
            "scope IN ('workspace', 'personal')",
            name="ck_skill_scope",
        ),
    )

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
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="upload", server_default="upload"
    )
    activation_default: Mapped[str] = mapped_column(
        String(32), nullable=False, default="on_demand", server_default="on_demand"
    )
    # 'workspace': visible to every member (default; preserves pre-0022
    # behavior). 'personal': only the creator can see / install / use it.
    # The web API + agent tools both filter on this so the visibility
    # boundary is consistent.
    scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default="workspace", server_default="workspace"
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
        CheckConstraint(
            "activation_override IS NULL OR activation_override IN "
            "('always_active', 'on_demand')",
            name="ck_skill_install_activation_override",
        ),
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
    activation_override: Mapped[str | None] = mapped_column(String(32), nullable=True)
    installed_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )


class IntegrationConnection(Base):
    """A connected app for a workspace, served by one of N providers (Pipedream
    today, Composio added in slice 0015, MCP-direct possibly later). Stores ONLY
    the provider-side connected-account reference; credentials live in the
    provider, never here.

    `provider` is decided at connect-time (preference: Composio if the app is
    in their catalogue, else Pipedream) and persisted so the gateway doesn't
    re-route on every action call. The `pipedream_account_id` column name is
    legacy; for Composio rows the field stores Composio's connection id under
    the same column to avoid a per-provider account id column."""

    __tablename__ = "integration_connection"
    __table_args__ = (
        UniqueConstraint("workspace_id", "app"),
        CheckConstraint(
            "provider IN ('pipedream', 'composio')",
            name="ck_integration_connection_provider",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    app: Mapped[str] = mapped_column(String(64), nullable=False)
    # Which integration provider backs this connection. Decided at connect-time.
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pipedream", server_default="pipedream"
    )
    # Empty until connected; "pending" while awaiting an in-conversation connect.
    # The column name is historical (was Pipedream-only); for Composio rows the
    # field stores Composio's connected_account_id under the same name. The
    # provider field above is the source of truth for which shape this is.
    pipedream_account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="connected")
    # In-conversation connect flow: the paused run to auto-resume once connected.
    pending_run_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    pending_ctx: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Per-tenant direct credentials for apps where the upstream provider's
    # wrapper is broken (Composio strips required fields, etc.) and we have
    # to bypass and call the app's REST API ourselves. JSON shape depends on
    # the app (Metabase: {api_key, base_url}, others TBD). Fernet-encrypted
    # at rest with WORKSPACE_TOKEN_ENCRYPTION_KEY; null for connections that
    # don't need direct access.
    direct_credentials_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # IANA timezone name from Slack's users.info `tz` field (e.g.
    # "America/Bogota"). Cached so the scheduled-tasks tool can default new
    # tasks to the calling user's tz without an extra round-trip. Null for
    # bots / deleted users / freshly added accounts without a tz set.
    tz: Mapped[str | None] = mapped_column(Text, nullable=True)
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


# Allowed values for scheduled_task discriminators. Same VARCHAR + CHECK
# convention as Skill / IntegrationConnection (see _SKILL_SOURCES comment above):
# Postgres ENUMs trip Alembic + asyncpg on partial-failure replays.
_TASK_SCOPES = ("local", "global", "system")
_TASK_DESTINATIONS = ("channel", "dm")
_TASK_RUN_STATUSES = ("success", "failed", "running")


class ScheduledTask(TimestampMixin, Base):
    """A cron-driven task. The scheduler fires it at `next_run_at`, opens a
    fresh thread on the configured destination, and runs the agent with
    `prompt` as the seed user message.

    Scope semantics:
    - `local`: owned by `owner_user_id`; only that user can edit / pause /
      delete. Fires into a DM with the owner (destination_type='dm') or a
      channel the owner picks.
    - `global`: workspace-wide (created by a user with edit rights for the
      whole workspace). Owner_user_id is null. Reserved for a later slice;
      the create tool literal restricts to 'local' in v1.
    - `system`: seeded by Misterr (workflow-discovery, daily-brief). No human
      owner; cannot be deleted, cannot have its prompt / cron / timezone
      changed. Only destination_slack_id is mutable in v1.

    `next_run_at` is computed at write time from `cron_spec + timezone` and
    advanced after every fire. The scheduler scans this column under a partial
    index that excludes paused rows; `FOR UPDATE SKIP LOCKED` is the only
    idempotency primitive (see scheduler.py)."""

    __tablename__ = "scheduled_task"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_scheduled_task_workspace_name"),
        CheckConstraint(
            "scope IN ('local', 'global', 'system')",
            name="ck_scheduled_task_scope",
        ),
        CheckConstraint(
            "destination_type IN ('channel', 'dm')",
            name="ck_scheduled_task_destination_type",
        ),
        CheckConstraint(
            "last_run_status IS NULL OR last_run_status IN ('success', 'failed', 'running')",
            name="ck_scheduled_task_last_run_status",
        ),
        CheckConstraint(
            "(scope = 'local' AND owner_user_id IS NOT NULL) "
            "OR (scope IN ('global', 'system') AND owner_user_id IS NULL)",
            name="ck_scheduled_task_scope_owner",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=True
    )
    # Slug, kebab-case in practice, unique per workspace. Used as a friendly
    # handle in tools (`pause_scheduled_task name='daily-revops-report'`).
    name: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    cron_spec: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    destination_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # Slack id of the channel (CXXX) or user (UXXX) where the run posts.
    # Nullable so a system task seeded before bot_home_channel_id is set still
    # validates; the scheduler then marks the fire failed without disabling
    # the task, so a later admin config recovers automatically.
    destination_slack_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # True for "send X once at time Y" tasks. The scheduler deletes the row
    # after the first successful fire instead of advancing next_run_at, so
    # one-shot delays don't accidentally recur (e.g. cron `55 16 30 5 *` for
    # "in 5 min on May 30" would otherwise fire every May 30 at that minute).
    fire_once: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # True when `prompt` is the LITERAL TEXT to post (no agent involvement).
    # send_delayed_message sets this. The scheduler bypasses run_agent and
    # calls chat.postMessage directly with `prompt` as the body. False (the
    # default) means `prompt` is a task description the agent has to execute
    # at fire time. Orthogonal to `fire_once` -- a one-shot agentic task
    # (fire_once=T, prompt_is_literal=F) is legal and distinct from a
    # one-shot literal delivery (fire_once=T, prompt_is_literal=T).
    prompt_is_literal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # When set in the future, the task is dormant until that timestamp; the
    # scheduler auto-resumes once `paused_until <= now()`. When NULL with
    # is_paused=true, the task is paused indefinitely (user must resume).
    #
    # All three timestamps below MUST be TIMESTAMP WITH TIME ZONE to match
    # migration 0017 and the aware datetimes the scheduler / repo pass in
    # (datetime.now(timezone.utc) + croniter results). Omitting timezone=True
    # makes SQLAlchemy infer TIMESTAMP WITHOUT TIME ZONE and asyncpg blows
    # up when it tries to coerce an aware value into a naive bind parameter
    # (`can't subtract offset-naive and offset-aware datetimes`).
    paused_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_run_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Short summary (1-3 sentences) of what the previous run produced; fed
    # into the next run as context so e.g. workflow-discovery can dedupe its
    # suggestions across executions. Set by the scheduler on a successful run.
    last_run_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ScheduledTaskRun(Base):
    """One row per execution of a scheduled_task. Persists across the parent
    task's lifecycle (ON DELETE SET NULL) so the UI can render history for
    deleted / completed one-shot tasks. Created when the scheduler claims
    the task (status='running') and finalized when the agent run or literal
    post returns (status='success' or 'failed')."""

    __tablename__ = "scheduled_task_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'success', 'failed')",
            name="ck_scheduled_task_run_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scheduled_task.id", ondelete="SET NULL"), nullable=True, index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


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


class SkillPreview(Base):
    """Skill upload waiting on the user's Install / Edit / Cancel click.

    Persisted (not in-memory) so previews survive backend restarts and the
    `/misterr skill upload` flow doesn't break every time Render redeploys.
    Scoped to (workspace_id, app_user_id); the row's UUID is the
    `preview_id` embedded in each block-kit `action_id`. A background task
    in `app/main.py` lifespan deletes expired rows periodically.

    No TimestampMixin: this is a short-lived row (30 min TTL) so the
    autoupdated `updated_at` is overkill; `created_at` + `expires_at`
    cover everything we need.
    """

    __tablename__ = "skill_preview"
    __table_args__ = (
        CheckConstraint(
            "activation IN ('always_active', 'on_demand')",
            name="ck_skill_preview_activation",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Slack identifiers stored alongside the FKs so action handlers route
    # without joining back to workspace / app_user just to post a message.
    slack_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(32), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    # Editable in the modal: name, description, activation. Initial values
    # come from the frontmatter generator (LLM + filename fallback).
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    activation: Mapped[str] = mapped_column(String(32), nullable=False)
    # Raw markdown body, up to 256 KB (cap enforced at intake).
    body: Mapped[str] = mapped_column(Text, nullable=False)
    links: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    inferred_fields: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
