"""concurrency: slack_event_seen + thread_inbox

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Dedupe of Slack event deliveries (at-least-once protection).
    op.create_table(
        "slack_event_seen",
        sa.Column("event_id", sa.String(length=128), primary_key=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_slack_event_seen_created_at", "slack_event_seen", ["created_at"])

    # Per-thread inbox: messages received while the thread's mutex was held.
    op.create_table(
        "thread_inbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", sa.String(length=32), nullable=False),
        sa.Column("conv_key", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_thread_inbox_thread_created",
        "thread_inbox",
        ["team_id", "conv_key", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_thread_inbox_thread_created", table_name="thread_inbox")
    op.drop_table("thread_inbox")
    op.drop_index("ix_slack_event_seen_created_at", table_name="slack_event_seen")
    op.drop_table("slack_event_seen")
