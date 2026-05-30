"""scheduled_task table + workspace.bot_home_channel_id

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-30

Slice T-1: backend for the scheduled-tasks feature. Users create cron-driven
tasks via the agent (`create_scheduled_task` tool); a background loop fires
them at their `next_run_at`, opens a fresh thread, and runs the agent with
the task's prompt as the seed user message.

Two system tasks are seeded per workspace (workflow-discovery + daily-brief).
The constants live in `app/scheduled_tasks/system_defaults.py`; this migration
just creates the table. Seeding happens at install time (slack install_store)
and idempotently at app startup (`ON CONFLICT (workspace_id, name) DO NOTHING`).

Why VARCHAR + CheckConstraint instead of Postgres ENUMs for scope /
destination_type / last_run_status: consistent with the rest of the schema
(see comment on `_SKILL_SOURCES` in models.py). SQLAlchemy 2.x + asyncpg +
Alembic interact badly on enum-type migrations.

`scope='global'` is reserved for a later slice that introduces workspace roles;
the column allows the value but no v1 code path can create one (the agent tool's
input literal restricts to 'local').

Indexes:
- UNIQUE (workspace_id, name): the resolver looks up by slug within a workspace.
- partial on next_run_at WHERE NOT paused AND next_run_at IS NOT NULL: scheduler
  scan path, keeps the index tight.
- (workspace_id, scope): "list system tasks" + "list global tasks" filters.
- partial on owner_user_id WHERE scope='local': "my tasks" filter in the UI.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # bot_home_channel_id on workspace: the channel where the bot was installed.
    # Nullable: legacy workspaces installed before this slice won't have it set
    # until the next install / a manual admin action. System tasks treat NULL
    # as "no destination" and mark the run as failed without auto-pausing, so
    # admins can configure later and the next tick recovers.
    op.add_column(
        "workspace",
        sa.Column("bot_home_channel_id", sa.Text(), nullable=True),
    )

    op.create_table(
        "scheduled_task",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(16), nullable=False),
        # created_by is null only for system tasks (no human owner). For local
        # we set it to the creating user; for global (future) the creator
        # admin.
        sa.Column(
            "created_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # owner is set only for scope='local'; for global/system it's null.
        # CASCADE because if the owner leaves the workspace, their personal
        # tasks should disappear with them.
        sa.Column(
            "owner_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("cron_spec", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("destination_type", sa.String(16), nullable=False),
        sa.Column("destination_slack_id", sa.Text(), nullable=True),
        sa.Column(
            "is_paused",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "paused_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.String(16), nullable=True),
        sa.Column("last_run_error", sa.Text(), nullable=True),
        sa.Column("last_run_summary", sa.Text(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("workspace_id", "name", name="uq_scheduled_task_workspace_name"),
        sa.CheckConstraint(
            "scope IN ('local', 'global', 'system')",
            name="ck_scheduled_task_scope",
        ),
        sa.CheckConstraint(
            "destination_type IN ('channel', 'dm')",
            name="ck_scheduled_task_destination_type",
        ),
        sa.CheckConstraint(
            "last_run_status IS NULL OR last_run_status IN ('success', 'failed', 'running')",
            name="ck_scheduled_task_last_run_status",
        ),
        # Invariants on scope <-> owner_user_id:
        #   local: owner_user_id NOT NULL
        #   global/system: owner_user_id NULL
        sa.CheckConstraint(
            "(scope = 'local' AND owner_user_id IS NOT NULL) "
            "OR (scope IN ('global', 'system') AND owner_user_id IS NULL)",
            name="ck_scheduled_task_scope_owner",
        ),
    )

    # Partial index on the scheduler scan path. Only rows that are eligible
    # appear; paused rows + rows without a next_run_at are excluded, keeping
    # the index small.
    op.create_index(
        "ix_scheduled_task_next_run_at_due",
        "scheduled_task",
        ["next_run_at"],
        postgresql_where=sa.text(
            "is_paused = false AND next_run_at IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_scheduled_task_workspace_scope",
        "scheduled_task",
        ["workspace_id", "scope"],
    )
    # "My tasks" filter: local tasks owned by a given user. Partial because
    # global/system never have owner_user_id set.
    op.create_index(
        "ix_scheduled_task_local_owner",
        "scheduled_task",
        ["owner_user_id"],
        postgresql_where=sa.text("scope = 'local'"),
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_task_local_owner", table_name="scheduled_task")
    op.drop_index("ix_scheduled_task_workspace_scope", table_name="scheduled_task")
    op.drop_index("ix_scheduled_task_next_run_at_due", table_name="scheduled_task")
    op.drop_table("scheduled_task")
    op.drop_column("workspace", "bot_home_channel_id")
