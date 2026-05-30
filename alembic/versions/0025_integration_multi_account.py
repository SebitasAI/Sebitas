"""integration_connection: multi-account + scope (team/private)

Revision ID: 0025
Revises: 0024
Create Date: 2026-05-30

Backs the Integrations page (slice T-6). Today a single integration_connection
per (workspace, app) means only ONE Google Drive per workspace, owned by
everyone. The web UI we're building lets users connect MULTIPLE accounts
per app, each scoped either "team" (everyone in the workspace can use it)
or "private" (only the owner can use it).

Schema changes (all additive + safe; existing rows default to scope='team',
owner_user_id=NULL, account_label=NULL -- matching today's behavior):

- Add `scope` ('team' | 'private'). NOT NULL DEFAULT 'team'.
- Add `owner_user_id` (FK app_user, ON DELETE SET NULL). Required when
  scope='private'; NULL when scope='team'.
- Add `account_label` (TEXT, nullable). Display name like "Team's Drive"
  or "Sam's personal Drive"; null for the default unnamed account.
- Drop the existing UNIQUE(workspace_id, app) constraint -- multi-account
  needs same (workspace, app) on several rows.
- Replace with TWO partial-unique indexes:
  * UNIQUE(workspace_id, app) WHERE scope='team' AND account_label IS NULL
    -> at most one un-labeled team connection per app.
  * UNIQUE(workspace_id, app, scope, COALESCE(owner_user_id::text, ''),
    COALESCE(account_label, '')) -> prevents exact duplicate labels per
    user per app.

CHECK constraint enforces the scope <-> owner_user_id invariant.

Tool-routing implications (handled in app/integrations/gateway.py, not
here): when `run_action` is called, the gateway picks a connection by
preferring the caller's private account over the team's, unless an
explicit `account_label` hint is passed.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "integration_connection",
        sa.Column(
            "scope",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'team'"),
        ),
    )
    op.add_column(
        "integration_connection",
        sa.Column(
            "owner_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "integration_connection",
        sa.Column("account_label", sa.Text(), nullable=True),
    )

    op.create_check_constraint(
        "ck_integration_connection_scope",
        "integration_connection",
        "scope IN ('team', 'private')",
    )
    op.create_check_constraint(
        "ck_integration_connection_scope_owner",
        "integration_connection",
        "(scope = 'team' AND owner_user_id IS NULL) "
        "OR (scope = 'private' AND owner_user_id IS NOT NULL)",
    )

    # Drop the old single-account-per-workspace-app constraint.
    op.drop_constraint(
        "integration_connection_workspace_id_app_key",
        "integration_connection",
        type_="unique",
    )

    # Replacement: at most ONE un-labeled team connection per (workspace, app).
    # Labeled team connections + private connections are allowed to coexist.
    op.create_index(
        "uq_integration_connection_team_default",
        "integration_connection",
        ["workspace_id", "app"],
        unique=True,
        postgresql_where=sa.text(
            "scope = 'team' AND account_label IS NULL"
        ),
    )
    # Prevent exact-duplicate labels per (workspace, app, scope, owner).
    # COALESCE so NULL labels and NULL owners collapse to ''; without this,
    # multiple NULL labels for the same (workspace, app, scope, owner)
    # would all be allowed.
    op.create_index(
        "uq_integration_connection_dedupe",
        "integration_connection",
        [
            "workspace_id",
            "app",
            "scope",
            sa.text("COALESCE(owner_user_id::text, '')"),
            sa.text("COALESCE(account_label, '')"),
        ],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_integration_connection_dedupe", table_name="integration_connection"
    )
    op.drop_index(
        "uq_integration_connection_team_default",
        table_name="integration_connection",
    )
    # Re-create the original single-account unique. Note: if multi-account
    # rows exist, this will fail -- intentional, downgrade requires manual
    # cleanup of duplicates first.
    op.create_unique_constraint(
        "integration_connection_workspace_id_app_key",
        "integration_connection",
        ["workspace_id", "app"],
    )
    op.drop_constraint(
        "ck_integration_connection_scope_owner",
        "integration_connection",
        type_="check",
    )
    op.drop_constraint(
        "ck_integration_connection_scope",
        "integration_connection",
        type_="check",
    )
    op.drop_column("integration_connection", "account_label")
    op.drop_column("integration_connection", "owner_user_id")
    op.drop_column("integration_connection", "scope")
