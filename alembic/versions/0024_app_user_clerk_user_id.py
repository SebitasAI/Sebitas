"""app_user.clerk_user_id

Revision ID: 0024
Revises: 0023
Create Date: 2026-05-30

Maps the AppUser <-> Clerk user identity (slice T-5). Once populated, the
backend's `require_app_user` Depends can find the right AppUser row from
a verified Clerk session via `clerk_user_id = JWT.sub` without falling
back to email matching.

Nullable: existing AppUser rows haven't been linked yet. The backfill
script `app/auth/migrations/backfill_clerk_orgs.py` resolves the link
via SlackUser.email -> Clerk user lookup. New web sign-ups attach at
first login.

UNIQUE per (workspace_id, clerk_user_id): one Clerk user can be a member
of multiple Slack workspaces (multi-workspace user) so we don't put a
global unique on clerk_user_id alone; but inside a workspace, one Clerk
user maps to exactly one AppUser.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column("clerk_user_id", sa.Text(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_app_user_workspace_clerk_user",
        "app_user",
        ["workspace_id", "clerk_user_id"],
    )
    # Lookup path: "is this Clerk user a member of any workspace?" -- the
    # require_app_user Depends scans for the (workspace_id, clerk_user_id)
    # combo. Partial index excludes the still-nullable legacy rows so the
    # index stays compact during the backfill window.
    op.create_index(
        "ix_app_user_clerk_user_id",
        "app_user",
        ["clerk_user_id"],
        postgresql_where=sa.text("clerk_user_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_app_user_clerk_user_id", table_name="app_user")
    op.drop_constraint("uq_app_user_workspace_clerk_user", "app_user", type_="unique")
    op.drop_column("app_user", "clerk_user_id")
