"""skill_preview: persist skill-upload preview state across backend restarts

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-29

The Skills upload flow keeps a "preview" state between when a user drops a
`.md` (and we run the LLM frontmatter generator) and when they click
Install / Edit / Cancel on the block-kit. Before this slice the preview
lived in a process-local dict, which meant every Render redeploy or restart
wiped pending previews and produced "La preview venció" errors.

Now the preview lives in Postgres, scoped to (workspace_id, app_user_id),
with a 30-minute TTL. A background task in app/main.py lifespan deletes
expired rows periodically.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skill_preview",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "app_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Slack-side identifiers stored alongside the FKs so action handlers
        # can route Slack API calls (chat_postEphemeral, response_url) without
        # an extra join back to workspace/app_user.
        sa.Column("slack_user_id", sa.String(length=32), nullable=False),
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        # Editable fields. The frontmatter generator fills these initially; the
        # Edit modal can mutate name/description/activation before Install.
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("activation", sa.String(length=32), nullable=False),
        # Raw markdown body of the upload, up to 256 KB (enforced upstream).
        sa.Column("body", sa.Text(), nullable=False),
        # [[link]] slugs extracted from the body.
        sa.Column("links", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        # Which fields were inferred by the LLM (vs supplied by the user).
        # Used by the preview UI to mark inferred values.
        sa.Column(
            "inferred_fields", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "activation IN ('always_active', 'on_demand')",
            name="ck_skill_preview_activation",
        ),
    )
    # Hot-path queries: tenant scoping on listing, expiry sweep on cleanup.
    op.create_index(
        "ix_skill_preview_workspace_user",
        "skill_preview",
        ["workspace_id", "app_user_id"],
    )
    op.create_index("ix_skill_preview_expires", "skill_preview", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_skill_preview_expires", table_name="skill_preview")
    op.drop_index("ix_skill_preview_workspace_user", table_name="skill_preview")
    op.drop_table("skill_preview")
