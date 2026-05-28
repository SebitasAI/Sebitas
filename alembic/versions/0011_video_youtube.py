"""attachments: video + youtube_link support

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "message_attachment",
        sa.Column(
            "attachment_type",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'file'"),
        ),
    )
    op.add_column(
        "message_attachment",
        sa.Column("metadata", postgresql.JSONB, nullable=True),
    )
    # youtube_link rows don't have an R2 file; relax the NOT NULL.
    op.alter_column("message_attachment", "r2_ref", existing_type=sa.String(length=512), nullable=True)


def downgrade() -> None:
    op.alter_column("message_attachment", "r2_ref", existing_type=sa.String(length=512), nullable=False)
    op.drop_column("message_attachment", "metadata")
    op.drop_column("message_attachment", "attachment_type")
