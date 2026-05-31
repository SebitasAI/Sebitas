"""scheduled_task_run: per-fire execution log

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-30

Persists every fire of a scheduled task (start + finish + outcome + output
snippet). Lets the web app render the run history per task, and survives
the parent task's deletion (`task_id` is ON DELETE SET NULL) so historical
runs of one-shot tasks remain visible even if the user later removes the
parent row.

Why not just keep last_run_* on scheduled_task: those columns only carry
the MOST RECENT run. Recurring tasks (daily-brief, workflow-discovery)
need a real history for the user to trust them. One-shots need at minimum
one persisted run row so the "Completed" card has something to expand.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scheduled_task_run",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # SET NULL: keep the row after the parent task is deleted so the
        # history remains visible (for one-shot completed/deleted tasks
        # especially). The denormalized fields below let the UI render the
        # log even when task_id is null.
        sa.Column(
            "task_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scheduled_task.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Snapshot so deleting/renaming the task doesn't break log readability.
        sa.Column("task_name_snapshot", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        # Full output of the fire: for literal-delivery this is the posted
        # text; for agentic runs it's the last assistant message. Truncated
        # to 4000 chars at write time to bound the column.
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed')",
            name="ck_scheduled_task_run_status",
        ),
    )
    op.create_index(
        "ix_scheduled_task_run_task_started",
        "scheduled_task_run",
        ["task_id", "started_at"],
        postgresql_using="btree",
    )
    op.create_index(
        "ix_scheduled_task_run_workspace_started",
        "scheduled_task_run",
        ["workspace_id", "started_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scheduled_task_run_workspace_started", table_name="scheduled_task_run"
    )
    op.drop_index(
        "ix_scheduled_task_run_task_started", table_name="scheduled_task_run"
    )
    op.drop_table("scheduled_task_run")
