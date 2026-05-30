"""scheduled_task.prompt_is_literal: split delivery semantics from deletion

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-30

`fire_once` was being overloaded as "delete after first fire" AND "bypass
the agent and post the prompt verbatim". Those are orthogonal:

  Three meaningful combinations:
  - recurring agentic (daily-brief, workflow-discovery): fire_once=F, literal=F
  - one-shot agentic ("en 2 min revisame el chat y avisame"): fire_once=T, literal=F
  - one-shot literal text (send_delayed_message): fire_once=T, literal=T

Without this column the third combination broke the second: a fire_once row
intended as agentic work was getting its prompt posted as literal text.

`prompt_is_literal` defaults to false so existing rows stay agentic. Only
send_delayed_message flips it to true.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scheduled_task",
        sa.Column(
            "prompt_is_literal",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("scheduled_task", "prompt_is_literal")
