"""automations: redesign as agnostic webhook-driven (drop 0027 shape)

Revision ID: 0028
Revises: 0027
Create Date: 2026-06-02

0027 created `automation` + `automation_run` with a hardcoded
`trigger_type` enum (agent_error / tool_failed / user_satisfaction_low /
scheduled_task_completed). That was a custom solution to a specific
"alert on failures" use case dressed up as infrastructure.

This migration replaces them with a source-driven model. An automation
has a `source` (direct / pipedream / composio) that says HOW Misterr
receives the trigger, and a `prompt_template` that runs the agent with
the webhook payload filled in. The user wires up the trigger condition
upstream (in Pipedream's catalog, in Composio's catalog, or by hand
POSTing to a direct URL) -- Misterr provides the inbound surface, not
the enumeration of supported events.

Why DROP + CREATE instead of ALTER:

The shape change is significant (column adds, removes, semantic flips
on trigger_event and action_type/action_config). ALTER would need a
data migration we don't need because:
  - The 0027 tables were deployed but have ZERO rows in prod (nobody
    used them; PR #93 went straight into the rewrite).
  - The downgrade path from 0028 back to 0027 isn't useful either --
    if we ever roll back, we'd want to roll all the way to 0026_memory
    rather than to the discarded 0027 shape.

DROP is safe. Downgrade re-creates the 0027 shape so the migration
chain is consistent, but realistically nobody will run it.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop everything from 0027. No data preservation -- the tables were
    # empty when this migration was authored (see module docstring).
    op.drop_index("ix_automation_run_workspace_started", table_name="automation_run")
    op.drop_index("ix_automation_run_automation_started", table_name="automation_run")
    op.drop_table("automation_run")
    op.drop_index("ix_automation_local_owner", table_name="automation")
    op.drop_index("ix_automation_workspace_trigger_active", table_name="automation")
    op.drop_table("automation")

    # New shape: source-driven, agnostic of WHAT triggers it.
    op.create_table(
        "automation",
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
        sa.Column(
            "created_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "owner_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # HOW we receive the trigger. The set of allowed values matches
        # the set of inbound webhook endpoints we expose:
        #   direct    -> /webhooks/auto/{webhook_secret}
        #   pipedream -> /webhooks/pipedream/{id}, HMAC per-trigger key
        #   composio  -> /webhooks/composio/{id}, HMAC account-wide key
        # Adding a fourth source means adding (a) a check value here,
        # (b) a webhook endpoint, (c) a verifier. The agent tools and
        # the action runner don't care about source.
        sa.Column("source", sa.String(16), nullable=False),
        # Templated prompt that fires the agent on each event. SafeDict
        # interpolation against the webhook payload (see automations/
        # actions.py); unknown keys stay literal `{key}` so a slightly
        # off template produces an ugly message instead of a failed run.
        sa.Column("prompt_template", sa.Text(), nullable=False),
        # Destination for the agent's reply. NULL = DM with the creator
        # (default). Slack channel id (C.../G...) or DM id (D...).
        sa.Column("destination_channel", sa.String(64), nullable=True),
        # source = direct: 32-byte base64url secret that lives inside
        # the inbound URL. Rotates via "regenerate URL".
        # source != direct: NULL.
        sa.Column("webhook_secret", sa.String(64), nullable=True),
        # source = pipedream/composio: opaque id returned by the
        # provider when we created the trigger on their side. We pass
        # it on delete so the provider's row goes away with ours.
        # source = direct: NULL.
        sa.Column("external_trigger_id", sa.String(128), nullable=True),
        # source = pipedream: per-trigger signing key Pipedream returns
        # once at trigger creation. Stored encrypted with the same
        # token crypto as bot_token (app/slack/crypto.py).
        # source = composio: NULL (Composio uses an account-wide secret
        # from Doppler, no per-trigger key).
        # source = direct: NULL.
        sa.Column("external_trigger_key_encrypted", sa.Text(), nullable=True),
        # Free-form metadata about the upstream trigger config: which
        # app, which event, any provider-side filters. Used by the UI
        # for display + by the chat preview before creation. Not
        # consulted at fire time.
        sa.Column(
            "trigger_metadata",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "scope",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'local'"),
        ),
        sa.Column(
            "is_paused",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fire_status", sa.String(16), nullable=True),
        sa.Column("last_fire_error", sa.Text(), nullable=True),
        sa.Column(
            "fire_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("workspace_id", "name", name="uq_automation_workspace_name"),
        sa.CheckConstraint(
            "source IN ('direct', 'pipedream', 'composio')",
            name="ck_automation_source",
        ),
        sa.CheckConstraint(
            "scope IN ('local', 'global', 'system')",
            name="ck_automation_scope",
        ),
        sa.CheckConstraint(
            "last_fire_status IS NULL OR last_fire_status IN "
            "('success', 'failed', 'skipped')",
            name="ck_automation_last_fire_status",
        ),
        sa.CheckConstraint(
            "(scope = 'local' AND owner_user_id IS NOT NULL) OR "
            "(scope IN ('global', 'system') AND owner_user_id IS NULL)",
            name="ck_automation_scope_owner",
        ),
        # source-specific required columns. Direct must have a webhook
        # secret; pipedream/composio must have an external_trigger_id;
        # pipedream additionally must have a signing key. The check
        # composes all three into one constraint -- Postgres only
        # surfaces one violation message, but the constraint name +
        # the source value let us debug from logs.
        sa.CheckConstraint(
            "(source = 'direct'    AND webhook_secret IS NOT NULL "
            "                       AND external_trigger_id IS NULL "
            "                       AND external_trigger_key_encrypted IS NULL) "
            "OR (source = 'pipedream' AND webhook_secret IS NULL "
            "                          AND external_trigger_id IS NOT NULL "
            "                          AND external_trigger_key_encrypted IS NOT NULL) "
            "OR (source = 'composio'  AND webhook_secret IS NULL "
            "                          AND external_trigger_id IS NOT NULL "
            "                          AND external_trigger_key_encrypted IS NULL)",
            name="ck_automation_source_columns",
        ),
        # The direct-source secret is the URL itself, so it has to be
        # unique even across workspaces (the route lookup is global on
        # the secret to keep the URL stateless). We model it as a
        # nullable unique constraint -- Postgres allows multiple NULLs.
        sa.UniqueConstraint("webhook_secret", name="uq_automation_webhook_secret"),
    )
    # Inbound lookups by URL secret hit this constraint's implicit
    # index, no extra index needed for direct webhooks.
    op.create_index(
        "ix_automation_local_owner",
        "automation",
        ["owner_user_id"],
        postgresql_where=sa.text("scope = 'local'"),
    )
    # External trigger lookups (pipedream/composio webhook handlers
    # resolve the automation by automation_id in the URL, not by
    # external_trigger_id -- but we still want this for ops queries +
    # for the cleanup-orphan path when a provider drops a trigger).
    op.create_index(
        "ix_automation_external_trigger",
        "automation",
        ["source", "external_trigger_id"],
        postgresql_where=sa.text("external_trigger_id IS NOT NULL"),
    )

    op.create_table(
        "automation_run",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "automation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("automation.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("automation_name_snapshot", sa.Text(), nullable=False),
        # Raw webhook payload that triggered this run. Snapshotted so
        # the run log stays meaningful after the upstream provider
        # rotates / changes shape.
        sa.Column(
            "trigger_payload",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column("prompt_template_snapshot", sa.Text(), nullable=False),
        sa.Column("rendered_prompt", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed', 'skipped')",
            name="ck_automation_run_status",
        ),
    )
    op.create_index(
        "ix_automation_run_automation_started",
        "automation_run",
        ["automation_id", "started_at"],
    )
    op.create_index(
        "ix_automation_run_workspace_started",
        "automation_run",
        ["workspace_id", "started_at"],
    )


def downgrade() -> None:
    # Drops the generic tables and re-creates the 0027 shape so the
    # alembic history is invertible. Realistically we'd downgrade to
    # 0026_memory (no automations at all) instead -- this exists for
    # completeness.
    op.drop_index("ix_automation_run_workspace_started", table_name="automation_run")
    op.drop_index("ix_automation_run_automation_started", table_name="automation_run")
    op.drop_table("automation_run")
    op.drop_index("ix_automation_external_trigger", table_name="automation")
    op.drop_index("ix_automation_local_owner", table_name="automation")
    op.drop_table("automation")

    # Re-create 0027 shape verbatim (paste from 0027_automations.py).
    op.create_table(
        "automation",
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
        sa.Column(
            "created_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "owner_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column(
            "trigger_filter",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("action_type", sa.String(16), nullable=False),
        sa.Column("action_config", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column(
            "scope",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'local'"),
        ),
        sa.Column(
            "is_paused",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fire_status", sa.String(16), nullable=True),
        sa.Column("last_fire_error", sa.Text(), nullable=True),
        sa.Column(
            "fire_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("workspace_id", "name", name="uq_automation_workspace_name"),
        sa.CheckConstraint(
            "trigger_type IN ("
            "'agent_error', 'tool_failed', 'user_satisfaction_low', "
            "'scheduled_task_completed')",
            name="ck_automation_trigger_type",
        ),
        sa.CheckConstraint(
            "action_type IN ('slack_notify', 'agent_run')",
            name="ck_automation_action_type",
        ),
        sa.CheckConstraint(
            "scope IN ('local', 'global', 'system')",
            name="ck_automation_scope",
        ),
        sa.CheckConstraint(
            "last_fire_status IS NULL OR last_fire_status IN "
            "('success', 'failed', 'skipped')",
            name="ck_automation_last_fire_status",
        ),
        sa.CheckConstraint(
            "(scope = 'local' AND owner_user_id IS NOT NULL) OR "
            "(scope IN ('global', 'system') AND owner_user_id IS NULL)",
            name="ck_automation_scope_owner",
        ),
    )
    op.create_index(
        "ix_automation_workspace_trigger_active",
        "automation",
        ["workspace_id", "trigger_type"],
        postgresql_where=sa.text("is_paused = false"),
    )
    op.create_index(
        "ix_automation_local_owner",
        "automation",
        ["owner_user_id"],
        postgresql_where=sa.text("scope = 'local'"),
    )
    op.create_table(
        "automation_run",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "automation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("automation.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("automation_name_snapshot", sa.Text(), nullable=False),
        sa.Column("trigger_event", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("action_type", sa.String(16), nullable=False),
        sa.Column(
            "action_config_snapshot",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed', 'skipped')",
            name="ck_automation_run_status",
        ),
    )
    op.create_index(
        "ix_automation_run_automation_started",
        "automation_run",
        ["automation_id", "started_at"],
    )
    op.create_index(
        "ix_automation_run_workspace_started",
        "automation_run",
        ["workspace_id", "started_at"],
    )
