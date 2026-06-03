"""workspace: welcome_dm_sent_at one-shot marker

Revision ID: 0032
Revises: 0031
Create Date: 2026-06-03

Slice-1 of the install-time welcome message. Adds a single nullable
timestamp column on `workspace` so the install path can deliver a
bienvenida DM exactly once per workspace.

Why a dedicated column instead of inferring "did we welcome them?" by
scanning the `message` table for a prior bot DM:

  - The check fires on every install attempt; scanning messages every
    time costs more than reading one indexed column.
  - The send path uses a conditional UPDATE
    `WHERE welcome_dm_sent_at IS NULL` so concurrent installs / retries
    can't double-deliver. That conditional needs the column.
  - Backfill is cheap: existing workspaces (installed before this
    slice) get a NULL value, which means "next install touches them ->
    welcome fires once". For workspaces we don't want to re-welcome,
    we can manually UPDATE the column to NOW() in prod after deploy.

No new index: the column is read by primary-key lookup of the
workspace row (already O(1) on `id`).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace",
        sa.Column(
            "welcome_dm_sent_at",
            sa.TIMESTAMP(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("workspace", "welcome_dm_sent_at")
