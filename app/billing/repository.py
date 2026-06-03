"""Billing data access. Reads + writes for the three local-state tables
(workspace_billing_plan, credit_balance, credit_ledger). Stripe glue
lives in `app/billing/stripe_client.py` (Slice 2).

The two operations the runner relies on:

  - `preflight_check`: read-only, called before spawning the agent.
    Returns a verdict the caller acts on (allow / block + message).
    Trivially passes for plans in `BYPASS_CREDIT_CHECK`.

  - `debit_for_agent_run`: row-locked write, called after a successful
    agent run completes. Atomically subtracts credits from the balance
    and appends a ledger row. Idempotent on (workspace_id, agent_run_id):
    if a debit for the same agent_run was already recorded, it's a
    no-op. This handles double-invocation if the finalize path retries.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.plans import BYPASS_CREDIT_CHECK, FREE_TIER_CREDITS_PER_MONTH, PLAN_FREE
from app.db.models import CreditBalance, CreditLedger, WorkspaceBillingPlan

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PreflightVerdict:
    """Result of the pre-flight balance check.

    `allowed`: True if the agent run may proceed.
    `plan`: the plan name, so the caller can show "Upgrade your Free
        plan" vs "Renew your Starter subscription".
    `balance_credits`: current balance, for the user-facing message.
    `reason`: short machine-readable code when `allowed=False`
        ('no_plan' | 'zero_balance').
    """

    allowed: bool
    plan: str
    balance_credits: float
    reason: str | None = None


async def get_plan(session: AsyncSession, workspace_id: uuid.UUID) -> WorkspaceBillingPlan | None:
    """Lookup the plan row by workspace. Returns None if the workspace
    doesn't have a billing row yet (new workspace pre-bootstrap)."""
    return await session.get(WorkspaceBillingPlan, workspace_id)


async def get_balance(session: AsyncSession, workspace_id: uuid.UUID) -> CreditBalance | None:
    return await session.get(CreditBalance, workspace_id)


async def ensure_bootstrapped(
    session: AsyncSession, workspace_id: uuid.UUID
) -> tuple[WorkspaceBillingPlan, CreditBalance]:
    """Make sure a workspace has a plan row + balance row. Used by the
    onboarding flow when a workspace is first created (post-migration).
    Defaults to `plan='free'` with the free-tier monthly credit allotment
    already loaded into balance, so a brand-new workspace can immediately
    chat with Misterr without an extra reset cron tick.

    Idempotent: if rows already exist (the migration backfilled them for
    every workspace), they're returned as-is."""
    plan = await session.get(WorkspaceBillingPlan, workspace_id)
    if plan is None:
        plan = WorkspaceBillingPlan(
            workspace_id=workspace_id,
            plan=PLAN_FREE,
            credits_per_month=FREE_TIER_CREDITS_PER_MONTH,
            price_usd_monthly=0,
        )
        session.add(plan)

    balance = await session.get(CreditBalance, workspace_id)
    if balance is None:
        balance = CreditBalance(
            workspace_id=workspace_id,
            balance_credits=FREE_TIER_CREDITS_PER_MONTH,
        )
        session.add(balance)
        # Audit row so the initial grant is visible in the ledger.
        session.add(
            CreditLedger(
                workspace_id=workspace_id,
                delta_credits=FREE_TIER_CREDITS_PER_MONTH,
                kind="initial_grant",
                balance_after_credits=FREE_TIER_CREDITS_PER_MONTH,
                note="Free tier initial grant",
            )
        )

    return plan, balance


async def preflight_check(
    session: AsyncSession, workspace_id: uuid.UUID
) -> PreflightVerdict:
    """Decide whether a new agent run may proceed for this workspace.

    Allow when: plan is in `BYPASS_CREDIT_CHECK` (unlimited, enterprise)
    OR balance > 0. Block otherwise with a structured reason for the
    caller's user-facing message.

    Edge case: when the workspace has no plan row (new workspace that
    skipped bootstrap somehow), we ALLOW and log a warning. Better to
    serve the customer than to block them on a self-inflicted bug --
    the balance debit will fail-soft a few ms later, but the user
    still gets a reply."""
    plan = await session.get(WorkspaceBillingPlan, workspace_id)
    if plan is None:
        log.warning(
            "billing_preflight_no_plan_row",
            workspace_id=str(workspace_id),
        )
        return PreflightVerdict(
            allowed=True, plan=PLAN_FREE, balance_credits=0.0, reason="no_plan"
        )

    if plan.plan in BYPASS_CREDIT_CHECK:
        return PreflightVerdict(
            allowed=True, plan=plan.plan, balance_credits=float("inf")
        )

    balance = await session.get(CreditBalance, workspace_id)
    balance_credits = float(balance.balance_credits) if balance else 0.0

    if balance_credits <= 0:
        return PreflightVerdict(
            allowed=False,
            plan=plan.plan,
            balance_credits=balance_credits,
            reason="zero_balance",
        )

    return PreflightVerdict(
        allowed=True, plan=plan.plan, balance_credits=balance_credits
    )


async def debit_for_agent_run(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_run_id: uuid.UUID,
    sales_cost_usd: float,
    credits_to_debit: float,
) -> CreditBalance | None:
    """Atomically subtract credits from the workspace balance and append
    a ledger row. Idempotent on `agent_run_id`: a repeat call for the
    same run is a no-op (returns the existing balance).

    Skipped (returns None) when the plan is in `BYPASS_CREDIT_CHECK`.
    Caller is responsible for committing the surrounding transaction;
    we don't commit here so the debit can be bundled into the same
    transaction as `_persist_agent_run`'s INSERT for atomicity.

    Negative balance allowed: if the cost slightly exceeds the balance
    at debit time, we let the run complete and leave the balance
    negative. The next pre-flight check will block. This trades a tiny
    bit of overage for the simpler invariant 'debit always matches
    actual cost'; in practice the overage is bounded by one run's
    sales_cost (typically <$1 USD)."""
    plan = await session.get(WorkspaceBillingPlan, workspace_id)
    if plan is None or plan.plan in BYPASS_CREDIT_CHECK:
        return None

    # Idempotency check: refuse to insert a second ledger entry for the
    # same agent_run.
    existing = await session.execute(
        select(CreditLedger.id).where(
            CreditLedger.workspace_id == workspace_id,
            CreditLedger.agent_run_id == agent_run_id,
            CreditLedger.kind == "debit_agent_run",
        )
    )
    if existing.scalar_one_or_none() is not None:
        return await session.get(CreditBalance, workspace_id)

    # Row-lock the balance to serialize concurrent debits. Two agent
    # runs finishing at the same time on the same workspace must not
    # race past each other's read.
    locked = await session.execute(
        select(CreditBalance)
        .where(CreditBalance.workspace_id == workspace_id)
        .with_for_update()
    )
    balance = locked.scalar_one_or_none()
    if balance is None:
        # Workspace exists but has no balance row -- shouldn't happen
        # after migration 0031's backfill, but defend against it.
        balance = CreditBalance(workspace_id=workspace_id, balance_credits=0)
        session.add(balance)
        await session.flush()

    new_balance = Decimal(str(balance.balance_credits)) - Decimal(str(credits_to_debit))
    balance.balance_credits = new_balance

    session.add(
        CreditLedger(
            workspace_id=workspace_id,
            delta_credits=-Decimal(str(credits_to_debit)),
            kind="debit_agent_run",
            agent_run_id=agent_run_id,
            sales_cost_usd=Decimal(str(sales_cost_usd)),
            balance_after_credits=new_balance,
            note=None,
        )
    )

    return balance


async def credit_monthly_reset(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    credits_to_grant: float,
    note: str | None = None,
) -> CreditBalance:
    """Wipe-and-grant the monthly allotment. The Slice 2 cron calls this
    once per month per active subscription. Resets `balance_credits` to
    exactly `credits_to_grant` (does NOT add to whatever was left over;
    credits don't roll over in MVP)."""
    locked = await session.execute(
        select(CreditBalance)
        .where(CreditBalance.workspace_id == workspace_id)
        .with_for_update()
    )
    balance = locked.scalar_one_or_none()
    if balance is None:
        balance = CreditBalance(workspace_id=workspace_id, balance_credits=0)
        session.add(balance)
        await session.flush()

    delta = Decimal(str(credits_to_grant)) - Decimal(str(balance.balance_credits))
    balance.balance_credits = Decimal(str(credits_to_grant))

    from datetime import datetime, timezone
    balance.last_reset_at = datetime.now(timezone.utc)

    session.add(
        CreditLedger(
            workspace_id=workspace_id,
            delta_credits=delta,
            kind="monthly_reset",
            balance_after_credits=Decimal(str(credits_to_grant)),
            note=note or "Monthly reset",
        )
    )
    return balance
