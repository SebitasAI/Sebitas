"""Daily worker that resets monthly credit allotments for annual subs.

Monthly subscriptions don't need this: Stripe fires
`invoice.payment_succeeded` once a month and `webhook_handlers.on_invoice_paid`
resets credits there. Annual subscriptions only get one invoice per year,
so the 11 intermediate months would otherwise never reset.

This loop runs every `RESET_TICK_SECONDS` (default: 6 hours) and:

  1. Finds active annual plan rows whose `credit_balance.last_reset_at`
     is more than ~30 days old.
  2. For each: calls `billing_repo.credit_monthly_reset` to wipe and
     grant `credits_per_month`.

We don't try to align resets to the calendar day of original signup
(no per-day cron). The 30-day-elapsed check is good enough for MVP;
customers don't notice if their reset happens at the wrong minute of
the day. Drift from the original signup date is bounded by
RESET_TICK_SECONDS.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import repository as billing_repo
from app.db.models import CreditBalance, WorkspaceBillingPlan
from app.db.session import get_session

log = structlog.get_logger(__name__)

# Loop tick. 6h gives <0.25-day drift on the monthly reset day
# while keeping the load near-zero (one query every 6h).
RESET_TICK_SECONDS = 6 * 60 * 60

# How long after last_reset_at do we consider a workspace due. Slight
# under 30 days to account for ticks landing slightly after the
# anniversary; over-granting by a few hours per month is OK.
RESET_INTERVAL = timedelta(days=29, hours=12)


async def _due_workspaces(db: AsyncSession) -> list[tuple[str, float]]:
    """Returns `(workspace_id, credits_per_month)` for every annual sub
    whose balance was last reset more than ~30 days ago, OR never
    reset (first run after annual signup)."""
    now = datetime.now(timezone.utc)
    cutoff = now - RESET_INTERVAL
    res = await db.execute(
        select(
            WorkspaceBillingPlan.workspace_id,
            WorkspaceBillingPlan.credits_per_month,
            CreditBalance.last_reset_at,
        )
        .join(CreditBalance, CreditBalance.workspace_id == WorkspaceBillingPlan.workspace_id)
        .where(
            WorkspaceBillingPlan.billing_cycle == "annual",
            WorkspaceBillingPlan.plan.notin_(("free", "unlimited", "enterprise")),
        )
    )
    due: list[tuple[str, float]] = []
    for ws_id, credits, last_reset in res.all():
        if last_reset is None or last_reset <= cutoff:
            due.append((str(ws_id), float(credits)))
    return due


async def tick() -> int:
    """One pass: find due workspaces, reset each. Returns the number
    of resets performed (for logging)."""
    async with get_session() as db:
        due = await _due_workspaces(db)
    if not due:
        return 0

    processed = 0
    for workspace_id, credits in due:
        try:
            async with get_session() as db:
                await billing_repo.credit_monthly_reset(
                    db,
                    workspace_id=workspace_id,  # repository handles UUID coercion
                    credits_to_grant=credits,
                    note="Annual subscription monthly tick",
                )
                await db.commit()
            processed += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "billing_monthly_reset_workspace_failed",
                workspace_id=workspace_id,
                error=str(exc)[:200],
            )
    return processed


async def run_monthly_reset_loop() -> None:
    """Forever loop, mounted by `app/main.py` lifespan. Cancellation
    is the only clean exit; tick exceptions are logged + swallowed."""
    log.info("billing_monthly_reset_started", tick_seconds=RESET_TICK_SECONDS)
    while True:
        try:
            processed = await tick()
            if processed:
                log.info("billing_monthly_reset_tick", processed=processed)
        except asyncio.CancelledError:
            log.info("billing_monthly_reset_cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("billing_monthly_reset_tick_errored", error=str(exc)[:500])
        try:
            await asyncio.sleep(RESET_TICK_SECONDS)
        except asyncio.CancelledError:
            log.info("billing_monthly_reset_cancelled")
            raise
