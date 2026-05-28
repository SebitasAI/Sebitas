"""spaces: space table (4B-i foundation)

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "space",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("convex_project_ref", sa.String(length=255), nullable=True),
        sa.Column("convex_deployment_ref", sa.String(length=255), nullable=True),
        sa.Column("frontend_url", sa.Text(), nullable=True),
        sa.Column("admin_key_vault_ref", sa.String(length=255), nullable=True),
        sa.Column("data_binding", postgresql.JSONB, nullable=False),
        sa.Column("access_list", postgresql.JSONB, nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("created_by_skill", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_space_workspace_id", "space", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_space_workspace_id", table_name="space")
    op.drop_table("space")
