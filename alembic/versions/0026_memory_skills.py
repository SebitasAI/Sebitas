"""skill.source allows 'memory' + partial index for memory slugs

Revision ID: 0026_memory
Revises: 0025
Create Date: 2026-06-02

Slice T-X (Phase A): persistent workspace memory built ON the existing
`skill` table via reserved slug conventions:
  - `company`    (scope=workspace) -> info de la empresa.
  - `team`       (scope=workspace) -> roles, canales, who-is-who.
  - `users/<U>`  (scope=personal, owner=that user) -> per-user memory.

Bodies are append-only logs (compaction is Phase C, not this slice). The
agent reads + writes via a new `remember` tool and the auto-load policy
in prompt_builder.

No new table. We only:
1. Extend the `ck_skill_source` CHECK to allow the value 'memory', so the
   memory skills are distinguishable from the regular 'upload' / 'catalog'
   ones. The web /api/skills endpoint filters these out (they're for the
   agent, not the user-facing skill list).
2. Add a partial index on (workspace_id, name) WHERE name matches a
   reserved memory slug -- the prompt_builder hits this every turn so the
   lookup needs to be cheap.

down_revision is "0025" rather than "0026" because another in-flight
branch (`0026_automations`) also chains from 0025. If that branch lands
first we'll have a multi-head; whoever merges second writes the
merge migration. Naming THIS revision "0026_memory" (not just "0026")
avoids a string collision.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0026_memory"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Extend the source CHECK to include 'memory'. Postgres can't ALTER a
    # CHECK constraint in place; drop + re-add. The constraint is small +
    # the rewrite is metadata-only (no row scan).
    op.drop_constraint("ck_skill_source", "skill", type_="check")
    op.create_check_constraint(
        "ck_skill_source",
        "skill",
        "source IN ('upload', 'catalog', 'memory')",
    )

    # Partial index: prompt_builder looks up by (workspace_id, name) for
    # exactly these slugs every agent turn. Keeping the index small (only
    # rows that match a memory slug) means cheap planning even when the
    # workspace has hundreds of regular skills.
    op.create_index(
        "ix_skill_memory_lookup",
        "skill",
        ["workspace_id", "name"],
        postgresql_where=sa.text(
            "name = 'company' OR name = 'team' OR name LIKE 'users/%'"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_skill_memory_lookup", table_name="skill")
    op.drop_constraint("ck_skill_source", "skill", type_="check")
    # NOTE: this downgrade strips 'memory' from the allowed values. Any
    # existing rows with source='memory' will violate the new constraint.
    # Caller MUST delete them first OR migrate to source='upload' before
    # running this downgrade. For safety we don't auto-mutate rows.
    op.create_check_constraint(
        "ck_skill_source",
        "skill",
        "source IN ('upload', 'catalog')",
    )
