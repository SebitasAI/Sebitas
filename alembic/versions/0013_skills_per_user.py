"""skills: drop legacy tables, recreate as workspace-scoped + per-user install

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-28

The previous shape (`skill.name` UNIQUE global + `skill_install` per-workspace)
modelled a global catalogue with per-workspace installs. The new shape, agreed
in the Skills per-user slice, is:

- `skill` lives inside a workspace (markdown body in R2, frontmatter inferred
  per-upload), unique by `(workspace_id, name)`.
- `skill_install` is per-user; each install can override the skill's default
  activation (`always_active` vs `on_demand`).

The legacy tables were a stub with no productive rows, so this migration is
destructive (drop + recreate). The downgrade restores the legacy shape so a
rollback leaves Alembic linear, though existing data is lost in both
directions (consistent with how the legacy tables were used).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotency: a previous deploy crashed mid-migration after creating the
    # new ENUM types (CREATE TYPE was not rolled back cleanly under asyncpg).
    # We now DROP IF EXISTS everything we're about to create, then recreate
    # fresh. Safe because the legacy stub had no productive rows (confirmed
    # at slice start) and the new tables haven't shipped to users yet.
    op.execute("DROP TABLE IF EXISTS skill_install CASCADE")
    op.execute("DROP TABLE IF EXISTS skill CASCADE")
    op.execute("DROP TYPE IF EXISTS skill_activation CASCADE")
    op.execute("DROP TYPE IF EXISTS skill_source CASCADE")

    # Postgres enums for the two discriminators. Created as types so the
    # column types pin them; the migration sets `create_type=False` on the
    # columns to avoid duplicate CREATE TYPE inside op.create_table.
    skill_source = sa.Enum("upload", "catalog", name="skill_source")
    skill_activation = sa.Enum("always_active", "on_demand", name="skill_activation")
    skill_source.create(op.get_bind(), checkfirst=False)
    skill_activation.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "skill",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("body_r2_ref", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "source",
            sa.Enum(
                "upload",
                "catalog",
                name="skill_source",
                create_type=False,
            ),
            nullable=False,
            server_default="upload",
        ),
        sa.Column(
            "activation_default",
            sa.Enum(
                "always_active",
                "on_demand",
                name="skill_activation",
                create_type=False,
            ),
            nullable=False,
            server_default="on_demand",
        ),
        sa.Column("links", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("workspace_id", "name", name="uq_skill_workspace_name"),
    )
    op.create_index(
        "ix_skill_workspace_created",
        "skill",
        ["workspace_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_skill_workspace_activation",
        "skill",
        ["workspace_id", "activation_default"],
    )

    op.create_table(
        "skill_install",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "skill_id",
            UUID(as_uuid=True),
            sa.ForeignKey("skill.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "activation_override",
            sa.Enum(
                "always_active",
                "on_demand",
                name="skill_activation",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column(
            "installed_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "skill_id", name="uq_skill_install_user_skill"),
    )
    op.create_index(
        "ix_skill_install_user_installed",
        "skill_install",
        ["user_id", sa.text("installed_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_skill_install_user_installed", table_name="skill_install")
    op.drop_table("skill_install")
    op.drop_index("ix_skill_workspace_activation", table_name="skill")
    op.drop_index("ix_skill_workspace_created", table_name="skill")
    op.drop_table("skill")

    # Drop enums after both tables that referenced them are gone.
    sa.Enum(name="skill_activation").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="skill_source").drop(op.get_bind(), checkfirst=True)

    # Recreate the legacy stub shape so the chain stays linear if someone
    # downgrades. No data restoration; the legacy tables were stubs.
    op.create_table(
        "skill",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=128), unique=True, nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("tags", JSONB(), nullable=True),
        sa.Column("author", sa.String(length=128), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("manifest_ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "skill_install",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "skill_id",
            UUID(as_uuid=True),
            sa.ForeignKey("skill.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "installed_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("workspace_id", "skill_id"),
    )
