"""integration_connection: add `provider` column for dual-provider routing

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-29

The integration gateway now routes between two providers (Pipedream and
Composio). When the app exists in both, Composio wins (per user decision);
fall back to Pipedream otherwise. The chosen provider is decided at
connect-time and persisted on the row so action invocations don't have to
re-decide.

Existing rows default to `pipedream` — that's where they were created.
CHECK constraint pins values to the known providers; adding a third
(MCP-direct, etc.) requires a follow-up migration.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "integration_connection",
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pipedream'"),
        ),
    )
    op.create_check_constraint(
        "ck_integration_connection_provider",
        "integration_connection",
        "provider IN ('pipedream', 'composio')",
    )
    # Index for the routing read path (read provider on every action call).
    op.create_index(
        "ix_integration_connection_workspace_app_provider",
        "integration_connection",
        ["workspace_id", "app", "provider"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_integration_connection_workspace_app_provider",
        table_name="integration_connection",
    )
    op.drop_constraint(
        "ck_integration_connection_provider",
        "integration_connection",
        type_="check",
    )
    op.drop_column("integration_connection", "provider")
