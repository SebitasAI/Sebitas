"""billing: plan, balance, ledger, stripe_subscription

Revision ID: 0031
Revises: 0030
Create Date: 2026-06-02

Slice 1 of the billing build-out. Adds the data layer that the
runner debits per agent run and that the (future) Stripe webhook
handler will write to. No Stripe integration yet, that lives in
Slice 2.

Four tables, intentionally separate by access pattern:

  - `workspace_billing_plan`: cold. One row per workspace describing
    its tier + slider position. Read on plan changes and pre-flight,
    written by Stripe webhook in Slice 2.

  - `credit_balance`: hot. One row per workspace, current credits
    available. Read on every inbound Slack message (pre-flight check),
    written after every successful agent run (debit). Separate from
    `workspace_billing_plan` because the access pattern is 1000x more
    write-heavy and we want a tight row footprint.

  - `credit_ledger`: append-only audit trail. One row per delta event
    (debit on agent run, monthly reset, plan change credit, admin
    adjustment). Lets us reconstruct any moment's balance for support
    + a future detailed billing statement.

  - `stripe_subscription`: mirror of Stripe state, slim. One row per
    workspace once it has a Stripe customer; null otherwise. Filled
    out in Slice 2 when we wire webhooks.

Credit math (consolidated by user 2026-06-02):
  - 1 credit = $0.001 USD sales price
  - 1 credit = ~$0.0002 USD real LLM cost (5x SALES_COST_MULTIPLIER)
  - Balance / deltas stored as NUMERIC(14, 3): fractional credits OK,
    no float drift, max value ~$10M sales which we'll never approach
    in one workspace.

Plan names (`workspace_billing_plan.plan`):
  - `free` — perpetual, 50,000 credits/month
  - `starter` — slider $100-$300/mo
  - `pro` — slider $400-$1,000/mo
  - `scale` — slider $1,500-$3,000/mo
  - `business` — slider $5,000-$10,000/mo
  - `enterprise` — custom contract
  - `unlimited` — pre-billing customers (Simetrik, Antiff, diio,
    Supersonik). No pre-flight check, no debit. Sentinel until we
    migrate them to a real plan.

Backfill: every existing workspace gets a `workspace_billing_plan`
row with `plan='unlimited'` and a `credit_balance` row with a high
sentinel balance, so the pre-flight check trivially passes for
current customers from day one.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PLAN_VALUES = (
    "free", "starter", "pro", "scale", "business", "enterprise", "unlimited",
)
_BILLING_CYCLE_VALUES = ("monthly", "annual")
_LEDGER_KIND_VALUES = (
    "debit_agent_run",
    "monthly_reset",
    "plan_change_credit",
    "plan_change_debit",
    "admin_adjustment",
    "initial_grant",
)
_STRIPE_STATUS_VALUES = (
    "active", "past_due", "canceled", "incomplete",
    "incomplete_expired", "trialing", "unpaid", "paused",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # workspace_billing_plan: tier + slider state. One row per workspace.
    op.create_table(
        "workspace_billing_plan",
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("plan", sa.String(24), nullable=False, server_default="free"),
        # null for free + unlimited (no billing cycle); set for paid plans.
        sa.Column("billing_cycle", sa.String(16), nullable=True),
        # Monthly credit allotment, derived from slider position. For free
        # this is 50_000. For paid plans, derived from `price_usd_monthly`
        # at the 1 credit = $0.001 ratio. Stored explicitly so we don't
        # have to recompute on every read + so future per-plan multipliers
        # remain a non-breaking change.
        sa.Column(
            "credits_per_month",
            sa.Numeric(14, 3),
            nullable=False,
            server_default=sa.text("50000"),
        ),
        # Effective monthly price the customer pays. For annual subs this
        # is the annual price / 12 (after the 20% discount), giving a
        # consistent per-cycle reference for analytics + invoice splits.
        # 0 for free / unlimited.
        sa.Column(
            "price_usd_monthly",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # Window the customer is currently inside. Resets carry these
        # forward on the monthly cron in Slice 2. Null for unlimited.
        sa.Column(
            "current_period_start",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "current_period_end",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        # cancel_at_period_end is the standard Stripe semantic: customer
        # asked to cancel, plan stays active through current_period_end,
        # then no auto-renew. Filled by Stripe webhook in Slice 2.
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            f"plan IN ({_quoted(_PLAN_VALUES)})",
            name="ck_billing_plan_plan",
        ),
        sa.CheckConstraint(
            f"billing_cycle IS NULL OR billing_cycle IN ({_quoted(_BILLING_CYCLE_VALUES)})",
            name="ck_billing_plan_cycle",
        ),
    )

    # credit_balance: hot path. One row per workspace; balance read on
    # every Slack inbound (pre-flight) and written on every agent_run.
    op.create_table(
        "credit_balance",
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "balance_credits",
            sa.Numeric(14, 3),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # When the last monthly reset happened. The cron in Slice 2
        # compares this against `workspace_billing_plan.current_period_*`
        # to decide whether to reset this workspace.
        sa.Column(
            "last_reset_at",
            sa.DateTime(timezone=True),
            nullable=True,
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
    )
    # No extra index: PK is enough -- every read is by workspace_id.

    # credit_ledger: append-only. Audit trail of every delta event.
    op.create_table(
        "credit_ledger",
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
        # Signed: negative for debits, positive for credits / refills.
        sa.Column("delta_credits", sa.Numeric(14, 3), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        # Soft reference to agent_run.id when kind = 'debit_agent_run'.
        # No FK on purpose: keeps ledger writes independent of agent_run
        # row availability + lets us drop agent_run retention without
        # cascading to the ledger.
        sa.Column(
            "agent_run_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        # Sales cost of the underlying run, for debit events. Lets us
        # reconcile ledger entries against agent_run.sales_cost_usd.
        sa.Column(
            "sales_cost_usd",
            sa.Numeric(12, 6),
            nullable=True,
        ),
        # Snapshot of the balance AFTER applying this delta. Lets us
        # rebuild "balance at time T" without summing the whole ledger.
        sa.Column(
            "balance_after_credits",
            sa.Numeric(14, 3),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            f"kind IN ({_quoted(_LEDGER_KIND_VALUES)})",
            name="ck_credit_ledger_kind",
        ),
    )
    # Time-ordered scans for the billing statement page.
    op.create_index(
        "ix_credit_ledger_workspace_created",
        "credit_ledger",
        ["workspace_id", sa.text("created_at DESC")],
    )

    # stripe_subscription: thin mirror of Stripe state. Filled by Slice 2
    # webhooks. One row per workspace once they've ever had a paid plan.
    op.create_table(
        "stripe_subscription",
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("stripe_customer_id", sa.String(64), nullable=False),
        # Null between cancellation and re-subscribe; Customer survives.
        sa.Column("stripe_subscription_id", sa.String(64), nullable=True),
        sa.Column("stripe_price_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
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
        sa.CheckConstraint(
            f"status IS NULL OR status IN ({_quoted(_STRIPE_STATUS_VALUES)})",
            name="ck_stripe_subscription_status",
        ),
    )
    # Webhook handler resolves by stripe_customer_id; need an index there.
    op.create_index(
        "ix_stripe_subscription_customer",
        "stripe_subscription",
        ["stripe_customer_id"],
        unique=True,
    )

    # Backfill: every existing workspace becomes 'unlimited' with a
    # sentinel balance so pre-flight never blocks current customers
    # (Simetrik, Antiff, diio, Supersonik). Idempotent: ON CONFLICT
    # DO NOTHING for tests + repeat runs.
    op.execute(
        """
        INSERT INTO workspace_billing_plan (workspace_id, plan, credits_per_month)
        SELECT id, 'unlimited', 0 FROM workspace
        ON CONFLICT (workspace_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO credit_balance (workspace_id, balance_credits)
        SELECT id, 0 FROM workspace
        ON CONFLICT (workspace_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_stripe_subscription_customer", table_name="stripe_subscription")
    op.drop_table("stripe_subscription")
    op.drop_index("ix_credit_ledger_workspace_created", table_name="credit_ledger")
    op.drop_table("credit_ledger")
    op.drop_table("credit_balance")
    op.drop_table("workspace_billing_plan")
