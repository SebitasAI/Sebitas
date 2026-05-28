"""message: tool_calls + tool_call_id (agent tool turns)

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("message", sa.Column("tool_calls", postgresql.JSONB(), nullable=True))
    op.add_column("message", sa.Column("tool_call_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("message", "tool_call_id")
    op.drop_column("message", "tool_calls")
