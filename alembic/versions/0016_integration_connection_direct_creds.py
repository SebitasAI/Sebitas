"""integration_connection: add `direct_credentials_encrypted` column

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-29

Per-tenant credential storage for apps where the upstream provider's wrapper
is broken and we have to call the app's REST API directly. Stores a Fernet-
encrypted JSON blob (api_key, base_url, …) keyed by the same row that holds
the Composio/Pipedream connection. The same WORKSPACE_TOKEN_ENCRYPTION_KEY
that protects bot_token at rest also protects this column; key in Doppler.

Why a column on integration_connection, not a separate table:
- 1:1 with the connection — when a user disconnects we want the credentials
  to disappear with the row.
- Tenant-scoped automatically via workspace_id on the parent row.

Why JSON blob, not separate columns:
- Each app's credential shape is different (Metabase: api_key + base_url,
  HubSpot: api_key, Snowflake: account + user + private_key + …). Forcing a
  schema would mean another migration per app.

The bypass logic in composio_provider.run_action() reads this column when
calling actions in the "broken Composio wrapper" allowlist. Replaces the
env-var fallback added in #54 (METABASE_FALLBACK_*).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "integration_connection",
        sa.Column(
            "direct_credentials_encrypted",
            sa.Text(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("integration_connection", "direct_credentials_encrypted")
