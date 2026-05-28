"""integration_connection: pending_run_id + pending_ctx; status -> connected

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("integration_connection", sa.Column("pending_run_id", sa.String(length=256), nullable=True))
    op.add_column("integration_connection", sa.Column("pending_ctx", postgresql.JSONB(), nullable=True))
    op.alter_column("integration_connection", "pipedream_account_id", existing_type=sa.String(length=128), nullable=True)
    op.execute("UPDATE integration_connection SET status='connected' WHERE status='active'")
    op.alter_column("integration_connection", "status", server_default="connected")


def downgrade() -> None:
    op.alter_column("integration_connection", "status", server_default="active")
    op.alter_column("integration_connection", "pipedream_account_id", existing_type=sa.String(length=128), nullable=False)
    op.drop_column("integration_connection", "pending_ctx")
    op.drop_column("integration_connection", "pending_run_id")
