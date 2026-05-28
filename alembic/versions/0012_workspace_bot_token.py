"""workspace: per-tenant bot token (multi-workspace install)

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # bot_token stores Fernet-encrypted xoxb. 512 chars is plenty (Slack
    # bot tokens are ~80 chars + Fernet adds ~100 chars of overhead).
    op.add_column("workspace", sa.Column("bot_token", sa.String(length=512), nullable=True))
    op.add_column("workspace", sa.Column("bot_user_id", sa.String(length=32), nullable=True))
    op.add_column("workspace", sa.Column("bot_scopes", sa.String(length=1024), nullable=True))
    op.add_column("workspace", sa.Column("installed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("workspace", "installed_at")
    op.drop_column("workspace", "bot_scopes")
    op.drop_column("workspace", "bot_user_id")
    op.drop_column("workspace", "bot_token")
