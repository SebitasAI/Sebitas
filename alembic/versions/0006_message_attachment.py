"""attachments: message_attachment table

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "message_attachment",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slack_file_id", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("original_name", sa.String(length=512), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("r2_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["message.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", "slack_file_id"),
    )
    op.create_index("ix_message_attachment_message_id", "message_attachment", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_message_attachment_message_id", table_name="message_attachment")
    op.drop_table("message_attachment")
