"""automation + automation_run tables (event-driven hooks)

Revision ID: 0027
Revises: 0026_memory
Create Date: 2026-06-02

Slice T-X: backend for Automations. Peer-level to scheduled_tasks but
event-driven (push) rather than cron-driven (pull). An automation matches
an event (agent_error, tool_failed, user_satisfaction_low,
scheduled_task_completed) against an optional JSONB filter, then dispatches
an action (slack_notify or agent_run).

Why two tables (mirrors scheduled_task / scheduled_task_run):
- `automation`: configuration row. Edits via agent tools. Survives forever
  unless the user deletes it.
- `automation_run`: one row per fire. Persists across the parent's life
  (FK ON DELETE SET NULL) so the run log survives even if the user later
  deletes the automation. Useful for the future /automations web page +
  audit trail.

VARCHAR + CHECK throughout, following the project convention
(see `_SKILL_SOURCES` comment in models.py for why we avoid Postgres ENUMs).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0027"
down_revision: Union[str, None] = "0026_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "automation",
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
        sa.Column(
            "created_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "owner_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column(
            "trigger_filter",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("action_type", sa.String(16), nullable=False),
        sa.Column(
            "action_config",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column(
            "scope",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'local'"),
        ),
        sa.Column(
            "is_paused",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fire_status", sa.String(16), nullable=True),
        sa.Column("last_fire_error", sa.Text(), nullable=True),
        sa.Column(
            "fire_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
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
        sa.UniqueConstraint("workspace_id", "name", name="uq_automation_workspace_name"),
        sa.CheckConstraint(
            "trigger_type IN ("
            "'agent_error', 'tool_failed', 'user_satisfaction_low', "
            "'scheduled_task_completed')",
            name="ck_automation_trigger_type",
        ),
        sa.CheckConstraint(
            "action_type IN ('slack_notify', 'agent_run')",
            name="ck_automation_action_type",
        ),
        sa.CheckConstraint(
            "scope IN ('local', 'global', 'system')",
            name="ck_automation_scope",
        ),
        sa.CheckConstraint(
            "last_fire_status IS NULL OR last_fire_status IN "
            "('success', 'failed', 'skipped')",
            name="ck_automation_last_fire_status",
        ),
        sa.CheckConstraint(
            "(scope = 'local' AND owner_user_id IS NOT NULL) OR "
            "(scope IN ('global', 'system') AND owner_user_id IS NULL)",
            name="ck_automation_scope_owner",
        ),
    )
    # Scan path: matching events to active automations.
    op.create_index(
        "ix_automation_workspace_trigger_active",
        "automation",
        ["workspace_id", "trigger_type"],
        postgresql_where=sa.text("is_paused = false"),
    )
    # "My automations" filter.
    op.create_index(
        "ix_automation_local_owner",
        "automation",
        ["owner_user_id"],
        postgresql_where=sa.text("scope = 'local'"),
    )

    op.create_table(
        "automation_run",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "automation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("automation.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("automation_name_snapshot", sa.Text(), nullable=False),
        sa.Column(
            "trigger_event",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column("action_type", sa.String(16), nullable=False),
        sa.Column(
            "action_config_snapshot",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed', 'skipped')",
            name="ck_automation_run_status",
        ),
    )
    op.create_index(
        "ix_automation_run_automation_started",
        "automation_run",
        ["automation_id", "started_at"],
    )
    op.create_index(
        "ix_automation_run_workspace_started",
        "automation_run",
        ["workspace_id", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_automation_run_workspace_started", table_name="automation_run")
    op.drop_index("ix_automation_run_automation_started", table_name="automation_run")
    op.drop_table("automation_run")
    op.drop_index("ix_automation_local_owner", table_name="automation")
    op.drop_index("ix_automation_workspace_trigger_active", table_name="automation")
    op.drop_table("automation")
