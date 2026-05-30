"""workspace.clerk_org_id

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-30

Backs the Slack-workspace ↔ Clerk-organization 1:1 mapping (slice T-5).

Each Slack workspace where Misterr is installed gets a Clerk Organization
created programmatically. The org's id is stored here so the backend can
go FROM a verified Clerk session (which carries org_id in the JWT claims
when an org is active) TO the local Workspace row without an email-match
walk.

Nullable + UNIQUE: nullable so existing rows can stay during the gradual
migration window before the data backfill runs; UNIQUE so two workspaces
can never share the same Clerk org. The application-layer migration
script (`app/auth/migrations/backfill_clerk_orgs.py`) provisions an org
for every workspace where `bot_token IS NOT NULL` and sets the column.
After backfill we tighten the constraint to NOT NULL in a later slice
once we're confident no installed workspace is left without an org.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workspace",
        sa.Column("clerk_org_id", sa.Text(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_workspace_clerk_org_id",
        "workspace",
        ["clerk_org_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_workspace_clerk_org_id", "workspace", type_="unique")
    op.drop_column("workspace", "clerk_org_id")
