"""agent_run: persistent log of every agent invocation for the Usage UI

Revision ID: 0030
Revises: 0029
Create Date: 2026-06-02

Misterr already emits per-run cost scores into Langfuse
(`total_cost_usd`, `sales_cost_usd`) via `app/agent/cost.py`. That's
great for developer-facing observability but won't power a
customer-facing Usage dashboard with sub-100ms response times -- every
chart page would round-trip Langfuse's API, hit rate limits, and tie
the UI's uptime to a third party.

This migration creates an in-tenant row per agent run so the Usage
page (`/usage` in the web app) can aggregate locally with normal SQL.

Columns to highlight:

  - `kind`: how the run was triggered. Lets the daily-stacked-bar chart
    break credits down by category (Threads / Scheduled tasks /
    Automations / Media) without scanning JSONB. The four enum values
    are CHECK-constrained for the same reason the rest of the schema
    uses VARCHAR+CHECK over Postgres enums (see _SKILL_SOURCES note).

  - `parent_ref_id` (nullable UUID): for kind != 'slack_thread', this
    is the scheduled_task / automation row id that owns this run.
    Used to compute "Top scheduled tasks by credits" in the Overview
    tab + the per-task drill-down. No FK because the parent row can
    be deleted while we keep the audit trail (parallel to
    automation_run.automation_id semantics).

  - `total_cost_usd` + `sales_cost_usd`: snapshot from
    `_cost.finalize_run_accumulator()` at run end. `sales_cost_usd`
    is what the UI bills as credits (1 credit = $0.001).

  - `status`: 'success' / 'failed'. Failed runs still get logged so
    the Activity feed shows them. We don't track 'running' here
    because the row is only inserted at finalize time -- LangGraph's
    checkpointer handles mid-run state.

Indexes:
  - `(workspace_id, started_at DESC)` for the time-range pagination
    that every Usage tab does.
  - `(workspace_id, app_user_id, started_at DESC)` for Team tab + per-user filters.
  - `(workspace_id, kind, started_at DESC)` for category aggregations in Overview.
  - `(workspace_id, parent_ref_id, started_at DESC)` for per-task drill-down.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_run",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SET NULL on app_user delete so the audit trail survives a
        # user being removed from the workspace. UI falls back to
        # "Unknown user" when null (matches the mock).
        sa.Column(
            "app_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # How the run was triggered. Categories the Usage chart breaks
        # down by. `media` reserved for future media-generation tools
        # (image, audio) -- currently every Misterr run is one of the
        # other three kinds; we surface `media` with zero rows in v1
        # so adding media tools later is purely a write-side change.
        sa.Column("kind", sa.String(24), nullable=False),
        # For kind in (scheduled_task, automation): id of the parent
        # row. For slack_thread: null. For media: null in v1; if/when
        # we wire media-generation tools we might point at a media job.
        sa.Column(
            "parent_ref_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        # Snapshot of the parent row's name at run time, for the
        # Top-tasks list + the Activity feed. Survives parent delete.
        sa.Column("parent_name_snapshot", sa.Text(), nullable=True),
        # Slack-side identifiers for the Activity feed. Channel + ts
        # let the UI deep-link to the original message.
        sa.Column("slack_channel_id", sa.String(64), nullable=True),
        sa.Column("slack_thread_ts", sa.String(32), nullable=True),
        # Token counts -- the raw LLM usage. Useful for the future
        # "show me the model mix" breakdown.
        sa.Column(
            "input_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "output_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "cache_read_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "cache_write_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # Costs at run-finalize time. `total_cost_usd` is raw LLM
        # (what we pay Anthropic + LiteLLM); `sales_cost_usd` is what
        # the customer's credits represent (LLM * SALES_COST_MULTIPLIER).
        sa.Column(
            "total_cost_usd",
            sa.Numeric(12, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "sales_cost_usd",
            sa.Numeric(12, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # Free-form model:token-count breakdown for the future
        # "model mix" UI. JSONB so we can extend without migrations.
        sa.Column(
            "by_model",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # Run lifecycle. `status='running'` rows shouldn't exist (we
        # only insert at finalize) but we model it for safety if
        # someone writes mid-run later.
        sa.Column("status", sa.String(16), nullable=False),
        # Langfuse trace id for the developer-side deep link.
        sa.Column("langfuse_trace_id", sa.String(64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('slack_thread', 'scheduled_task', 'automation', 'media')",
            name="ck_agent_run_kind",
        ),
        sa.CheckConstraint(
            "status IN ('success', 'failed', 'running')",
            name="ck_agent_run_status",
        ),
    )
    # Hot path: every Usage tab filters by workspace + a date range.
    op.create_index(
        "ix_agent_run_workspace_started",
        "agent_run",
        ["workspace_id", sa.text("started_at DESC")],
    )
    # Team tab + per-user activity filter.
    op.create_index(
        "ix_agent_run_workspace_user_started",
        "agent_run",
        ["workspace_id", "app_user_id", sa.text("started_at DESC")],
    )
    # Overview daily-stacked-bar break-down by category.
    op.create_index(
        "ix_agent_run_workspace_kind_started",
        "agent_run",
        ["workspace_id", "kind", sa.text("started_at DESC")],
    )
    # Per-parent drill-down (top tasks, per-task history).
    op.create_index(
        "ix_agent_run_workspace_parent_started",
        "agent_run",
        ["workspace_id", "parent_ref_id", sa.text("started_at DESC")],
        postgresql_where=sa.text("parent_ref_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_run_workspace_parent_started", table_name="agent_run"
    )
    op.drop_index(
        "ix_agent_run_workspace_kind_started", table_name="agent_run"
    )
    op.drop_index(
        "ix_agent_run_workspace_user_started", table_name="agent_run"
    )
    op.drop_index(
        "ix_agent_run_workspace_started", table_name="agent_run"
    )
    op.drop_table("agent_run")
