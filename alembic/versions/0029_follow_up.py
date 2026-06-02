"""follow_up table for state-driven reactivation nudges

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-02

A follow_up is a deferred reminder Misterr schedules when it senses a
thread is waiting on user input. The agent calls `schedule_follow_up`
at end of a turn where it asked for something concrete and the
conversation will be blocked until the user responds. The follow-up
worker fires the nudge at `scheduled_for` UNLESS the user replied in
the thread in the meantime.

Why a new table instead of reusing scheduled_task:

- scheduled_task is cron-driven, recurring, user-owned. follow_up is
  one-shot, state-driven, agent-owned.
- scheduled_task fires fresh agent runs in the configured destination.
  follow_up posts back into the original thread where the agent was
  waiting, with auto-cancellation if the user already replied.
- The cancellation condition ("did the user reply") is unique to
  follow_ups; mixing it into the scheduled_task code path would add
  per-row branching everywhere.

Phase 1 (this migration): single nudge per follow_up. Phase 2 would
add escalation (re-nudge after 2x wait if still no reply, up to 3
total) -- the schema accommodates it via `nudge_count` already.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "follow_up",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "app_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # Where to post the nudge. For thread follow-ups, conversation_key
        # is the thread_ts; for DM follow-ups, it's the DM channel id.
        # reply_thread_ts is null when the nudge should land at channel root.
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("conversation_key", sa.Text(), nullable=False),
        sa.Column("reply_thread_ts", sa.Text(), nullable=True),
        # The agent's short description of what we're waiting for. Fed
        # back to the agent at fire time to compose the nudge.
        sa.Column("reason", sa.Text(), nullable=False),
        # When the nudge should fire (UTC). Worker scans this column.
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        # 'pending' -> agent-created, awaiting fire.
        # 'sent'    -> nudge posted to the user.
        # 'cancelled' -> user replied first (auto) or admin cancelled.
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        # Phase 1 fires once (nudge_count goes 0 -> 1 on send). Phase 2
        # may re-nudge with escalation; cap at 3.
        sa.Column(
            "nudge_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        # The agent run that opened this follow-up. For traceability +
        # to dedup ("did this same run already schedule one?").
        sa.Column("created_by_run_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_reason", sa.String(32), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'cancelled')",
            name="ck_follow_up_status",
        ),
        sa.CheckConstraint(
            "nudge_count >= 0 AND nudge_count <= 3",
            name="ck_follow_up_nudge_count",
        ),
    )

    # Partial index: the worker scans pending rows by scheduled_for. Past
    # rows (sent / cancelled) never need to be touched, so a partial
    # index keeps the working set tiny even as history accumulates.
    op.create_index(
        "ix_follow_up_pending_due",
        "follow_up",
        ["scheduled_for"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_follow_up_pending_due", table_name="follow_up")
    op.drop_table("follow_up")
