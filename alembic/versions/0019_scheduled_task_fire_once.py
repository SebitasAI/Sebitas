"""scheduled_task.fire_once for one-shot delayed messages

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-30

Adds a `fire_once` boolean to `scheduled_task` so the agent can model
"send X in N minutes" / "remind me tomorrow at 9" without leaning on
cron's recurring semantics. The scheduler DELETEs `fire_once` rows after
the first successful fire (instead of advancing `next_run_at`), so the
user doesn't end up with an annual recurring ping at the same minute.

Existing rows default to `fire_once=false` (recurring), matching today's
behavior.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scheduled_task",
        sa.Column(
            "fire_once",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("scheduled_task", "fire_once")
