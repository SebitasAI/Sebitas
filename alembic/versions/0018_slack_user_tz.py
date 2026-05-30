"""slack_user.tz

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-30

Adds the Slack profile timezone (`tz` field from `users.info`) to the cached
roster. With this column populated, the scheduled-tasks agent tool can
default the new task's timezone to the calling user's tz when the user
doesn't explicitly mention one, removing a friction step in chat:

  Sam: "todos los días a las 9am hazme el reporte"
  Misterr: (uses Sam's slack_user.tz=America/Bogota directly, no question)

Slack stores the IANA name in `user.tz` (e.g. "America/Bogota"). Nullable
because some users (deleted, bot, or freshly added) may not have a tz set.
The fallback chain in the scheduled_tasks timezone helper still ends at UTC.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("slack_user", sa.Column("tz", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("slack_user", "tz")
