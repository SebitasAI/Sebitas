"""workspace: slack_team_icon_url for the sidebar workspace selector

Revision ID: 0033
Revises: 0032
Create Date: 2026-06-03

The web sidebar's WorkspaceSelector already expects an `iconUrl` field
per workspace and falls back to a one-letter avatar when null. Today
the backend hardcodes `None` because the column doesn't exist; the
selector therefore always renders the fallback. This migration adds
the column so we can persist the Slack team icon (returned by
`team.info`) and surface a recognizable logo there.

Single nullable column, no index needed (only read by PK lookup of
the workspace row). Backfill is lazy: the next install or roster
sweep populates it for any workspace where the icon is still NULL.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workspace",
        sa.Column("slack_team_icon_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspace", "slack_team_icon_url")
