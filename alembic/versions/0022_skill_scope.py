"""skill.scope: workspace-wide vs personal skills

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-30

Adds a `scope` column to the `skill` table so users can keep "personal"
skills that aren't visible to the rest of the workspace. The agent and
web app filter visibility on read:

  - scope='workspace': any workspace member can see + install (today's
    behavior, hence the default).
  - scope='personal': only the user in `created_by_user_id` can see /
    install / use. The Slack agent enforces this at the tool layer; the
    web API enforces at /api/skills.

We use VARCHAR + CHECK (same convention as task_scope etc.) instead of
a Postgres ENUM so Alembic + asyncpg + SQLAlchemy 2 stay friendly.

Defaults existing rows to 'workspace' so nothing visible changes after
the migration.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "skill",
        sa.Column(
            "scope",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'workspace'"),
        ),
    )
    op.create_check_constraint(
        "ck_skill_scope",
        "skill",
        "scope IN ('workspace', 'personal')",
    )
    # Helpful for the web /api/skills lookup ("workspace skills + my
    # personals"). Partial because the personal-skills row count per user
    # is small.
    op.create_index(
        "ix_skill_personal_creator",
        "skill",
        ["created_by_user_id"],
        postgresql_where=sa.text("scope = 'personal'"),
    )


def downgrade() -> None:
    op.drop_index("ix_skill_personal_creator", table_name="skill")
    op.drop_constraint("ck_skill_scope", "skill", type_="check")
    op.drop_column("skill", "scope")
