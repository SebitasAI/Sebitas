"""Subscription lifecycle handlers, one per Stripe event type we
subscribe to. Each handler is the **single source** of truth for the
side effect that event implies; the webhook endpoint just verifies the
signature, parses, and dispatches by `event.type`.

Idempotency strategy: every event carries unique state references
(subscription.id, invoice.id, plan + period_start), and every handler
treats its side effect as a UPSERT-like operation by checking
"already at the target state?" before mutating. A Stripe retry of the
same event therefore lands as a no-op rather than a duplicate credit
grant or a duplicate ledger row.

Events we handle (6):
  - `checkout.session.completed`        first subscription created
  - `customer.subscription.created`     backup of above (admin-side subs)
  - `customer.subscription.updated`     plan change, slider, status
  - `customer.subscription.deleted`     canceled -> demote to free
  - `invoice.payment_succeeded`         monthly / annual payment -> grant
  - `invoice.payment_failed`            alert admin (no state change)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import repository as billing_repo
from app.billing.plans import (
    ALL_PLANS,
    PLAN_FREE,
    PLANS,
    credits_for_price,
)
from app.db.models import (
    CreditBalance,
    StripeSubscription,
    Workspace,
    WorkspaceBillingPlan,
)
from app.db.session import get_session

log = structlog.get_logger(__name__)


def _ts_to_dt(ts: int | None) -> datetime | None:
    """Stripe gives UNIX timestamps; we store DateTime(timezone=True)."""
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


def _meta_workspace_id(metadata: dict[str, Any] | None) -> uuid.UUID | None:
    if not metadata:
        return None
    raw = metadata.get("workspace_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


def _meta_plan(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    plan = metadata.get("plan")
    if not plan or plan not in ALL_PLANS:
        return None
    return str(plan)


def _meta_cycle(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    cycle = metadata.get("cycle")
    if cycle in ("monthly", "annual"):
        return str(cycle)
    return None


async def _resolve_workspace_from_customer(
    session: AsyncSession, customer_id: str
) -> uuid.UUID | None:
    """Fall back to the stripe_subscription mirror when an event's
    metadata is missing the workspace_id."""
    row = await session.execute(
        select(StripeSubscription.workspace_id).where(
            StripeSubscription.stripe_customer_id == customer_id,
        )
    )
    return row.scalar_one_or_none()


async def _upsert_subscription_row(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    customer_id: str,
    subscription_id: str | None,
    price_id: str | None,
    status: str | None,
) -> None:
    """Insert-or-update the stripe_subscription mirror. The mirror is
    intentionally narrow: just what the in-app pages need to render
    'current plan, active until X' without round-tripping Stripe."""
    existing = await session.get(StripeSubscription, workspace_id)
    if existing is None:
        session.add(
            StripeSubscription(
                workspace_id=workspace_id,
                stripe_customer_id=customer_id,
                stripe_subscription_id=subscription_id,
                stripe_price_id=price_id,
                status=status,
            )
        )
        return
    existing.stripe_customer_id = customer_id
    if subscription_id is not None:
        existing.stripe_subscription_id = subscription_id
    if price_id is not None:
        existing.stripe_price_id = price_id
    if status is not None:
        existing.status = status
    existing.updated_at = datetime.now(timezone.utc)


async def _apply_plan(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    plan: str,
    cycle: str | None,
    monthly_price: float,
    current_period_start: datetime | None,
    current_period_end: datetime | None,
    cancel_at_period_end: bool,
) -> tuple[WorkspaceBillingPlan, bool]:
    """Set the workspace's plan + slider state. Returns
    `(plan_row, plan_changed)` so the caller knows whether to grant
    fresh credits (only on actual plan transition; renewals are
    handled by the invoice handler)."""
    plan_row = await session.get(WorkspaceBillingPlan, workspace_id)
    plan_changed = False
    credits_per_month = (
        credits_for_price(monthly_price) if monthly_price > 0 else PLANS.get(plan, PLANS["free"]).credits_floor
    )
    if plan == PLAN_FREE:
        # Free monthly grant is fixed at 50k; ignore monthly_price.
        from app.billing.plans import FREE_TIER_CREDITS_PER_MONTH
        credits_per_month = FREE_TIER_CREDITS_PER_MONTH

    if plan_row is None:
        plan_row = WorkspaceBillingPlan(
            workspace_id=workspace_id,
            plan=plan,
            billing_cycle=cycle,
            credits_per_month=credits_per_month,
            price_usd_monthly=monthly_price,
            current_period_start=current_period_start,
            current_period_end=current_period_end,
            cancel_at_period_end=cancel_at_period_end,
        )
        session.add(plan_row)
        plan_changed = True
        return plan_row, plan_changed

    if plan_row.plan != plan:
        plan_changed = True
    plan_row.plan = plan
    plan_row.billing_cycle = cycle
    plan_row.credits_per_month = credits_per_month
    plan_row.price_usd_monthly = monthly_price
    plan_row.current_period_start = current_period_start
    plan_row.current_period_end = current_period_end
    plan_row.cancel_at_period_end = cancel_at_period_end
    plan_row.updated_at = datetime.now(timezone.utc)
    return plan_row, plan_changed


# --- Handlers ---------------------------------------------------------- #


async def on_checkout_completed(event: dict) -> None:
    """`checkout.session.completed` — customer just paid for a sub.

    Stripe also fires `customer.subscription.created` for the same
    transaction. Both handlers are idempotent + converge to the same
    state, so order doesn't matter. We do the work here when metadata
    is reliably present (Checkout always sets it; admin-created subs
    might not)."""
    session = event["data"]["object"]
    workspace_id = _meta_workspace_id(session.get("metadata"))
    customer_id = session.get("customer")
    subscription_id = session.get("subscription")
    plan = _meta_plan(session.get("metadata"))
    cycle = _meta_cycle(session.get("metadata")) or "monthly"
    if not (workspace_id and customer_id and subscription_id and plan):
        log.warning(
            "stripe_checkout_completed_skipped_missing_fields",
            workspace_id=str(workspace_id) if workspace_id else None,
            has_customer=bool(customer_id),
            has_subscription=bool(subscription_id),
            has_plan=bool(plan),
        )
        return

    # Resolve subscription details now so we can stamp period + price.
    from app.billing import stripe_client
    sub = await stripe_client.retrieve_subscription(subscription_id)
    price_id = None
    if sub.items and sub.items.data:
        price_id = sub.items.data[0].price.id
    monthly_price = _monthly_price_for_plan(plan)

    async with get_session() as db:
        await _upsert_subscription_row(
            db,
            workspace_id=workspace_id,
            customer_id=customer_id,
            subscription_id=subscription_id,
            price_id=price_id,
            status=getattr(sub, "status", None),
        )
        _, plan_changed = await _apply_plan(
            db,
            workspace_id=workspace_id,
            plan=plan,
            cycle=cycle,
            monthly_price=monthly_price,
            current_period_start=_ts_to_dt(getattr(sub, "current_period_start", None)),
            current_period_end=_ts_to_dt(getattr(sub, "current_period_end", None)),
            cancel_at_period_end=bool(getattr(sub, "cancel_at_period_end", False)),
        )
        if plan_changed:
            # Grant the new tier's monthly allotment on first activation.
            # Renewals don't pass through here; they go via invoice handler.
            await billing_repo.credit_monthly_reset(
                db,
                workspace_id=workspace_id,
                credits_to_grant=credits_for_price(monthly_price),
                note=f"Plan activation: {plan} ({cycle})",
            )
        await db.commit()
        log.info(
            "stripe_checkout_completed_applied",
            workspace_id=str(workspace_id), plan=plan, cycle=cycle,
            plan_changed=plan_changed,
        )


async def on_subscription_created(event: dict) -> None:
    """`customer.subscription.created` — backup of the checkout handler.

    Same side effect; runs in case the subscription was created outside
    of Checkout (e.g. admin dashboard, billing portal upgrade). The
    upsert + plan_changed gate makes the second fire of the same event
    a no-op."""
    sub = event["data"]["object"]
    workspace_id = _meta_workspace_id(sub.get("metadata"))
    customer_id = sub.get("customer")
    if not workspace_id and customer_id:
        async with get_session() as db:
            workspace_id = await _resolve_workspace_from_customer(db, customer_id)
    if not (workspace_id and customer_id):
        log.warning("stripe_sub_created_no_workspace")
        return

    plan = _meta_plan(sub.get("metadata")) or PLAN_FREE
    cycle = _meta_cycle(sub.get("metadata")) or "monthly"
    monthly_price = _monthly_price_for_plan(plan)
    price_id = None
    if sub.get("items", {}).get("data"):
        price_id = sub["items"]["data"][0]["price"]["id"]

    async with get_session() as db:
        await _upsert_subscription_row(
            db,
            workspace_id=workspace_id,
            customer_id=customer_id,
            subscription_id=sub.get("id"),
            price_id=price_id,
            status=sub.get("status"),
        )
        _, plan_changed = await _apply_plan(
            db,
            workspace_id=workspace_id,
            plan=plan,
            cycle=cycle,
            monthly_price=monthly_price,
            current_period_start=_ts_to_dt(sub.get("current_period_start")),
            current_period_end=_ts_to_dt(sub.get("current_period_end")),
            cancel_at_period_end=bool(sub.get("cancel_at_period_end")),
        )
        if plan_changed and plan != PLAN_FREE:
            await billing_repo.credit_monthly_reset(
                db,
                workspace_id=workspace_id,
                credits_to_grant=credits_for_price(monthly_price),
                note=f"Plan activation via subscription.created: {plan}",
            )
        await db.commit()


async def on_subscription_updated(event: dict) -> None:
    """`customer.subscription.updated` — plan change, slider, status.

    We update the plan row to mirror Stripe. If `cancel_at_period_end`
    flipped true, we don't change `plan` (customer still has access);
    only flip when `customer.subscription.deleted` fires.

    If the plan actually changed (e.g. customer upgraded mid-cycle),
    we grant credits for the NEW plan immediately. Stripe handles
    proration of the dollars; we mirror by topping up to the new
    allotment so the customer can immediately use what they paid for."""
    sub = event["data"]["object"]
    workspace_id = _meta_workspace_id(sub.get("metadata"))
    customer_id = sub.get("customer")
    if not workspace_id and customer_id:
        async with get_session() as db:
            workspace_id = await _resolve_workspace_from_customer(db, customer_id)
    if not workspace_id:
        log.warning("stripe_sub_updated_no_workspace")
        return

    plan = _meta_plan(sub.get("metadata")) or PLAN_FREE
    cycle = _meta_cycle(sub.get("metadata")) or "monthly"
    monthly_price = _monthly_price_for_plan(plan)
    price_id = None
    if sub.get("items", {}).get("data"):
        price_id = sub["items"]["data"][0]["price"]["id"]

    async with get_session() as db:
        await _upsert_subscription_row(
            db,
            workspace_id=workspace_id,
            customer_id=customer_id,
            subscription_id=sub.get("id"),
            price_id=price_id,
            status=sub.get("status"),
        )
        _, plan_changed = await _apply_plan(
            db,
            workspace_id=workspace_id,
            plan=plan,
            cycle=cycle,
            monthly_price=monthly_price,
            current_period_start=_ts_to_dt(sub.get("current_period_start")),
            current_period_end=_ts_to_dt(sub.get("current_period_end")),
            cancel_at_period_end=bool(sub.get("cancel_at_period_end")),
        )
        if plan_changed and plan != PLAN_FREE:
            await billing_repo.credit_monthly_reset(
                db,
                workspace_id=workspace_id,
                credits_to_grant=credits_for_price(monthly_price),
                note=f"Plan change to {plan}",
            )
        await db.commit()


async def on_subscription_deleted(event: dict) -> None:
    """`customer.subscription.deleted` — sub canceled. Demote to free.
    Keep the credit balance as-is so the customer can still use any
    leftover credits until next month's free-tier reset."""
    sub = event["data"]["object"]
    workspace_id = _meta_workspace_id(sub.get("metadata"))
    customer_id = sub.get("customer")
    if not workspace_id and customer_id:
        async with get_session() as db:
            workspace_id = await _resolve_workspace_from_customer(db, customer_id)
    if not workspace_id:
        log.warning("stripe_sub_deleted_no_workspace")
        return

    async with get_session() as db:
        plan_row = await db.get(WorkspaceBillingPlan, workspace_id)
        if plan_row is None:
            return
        plan_row.plan = PLAN_FREE
        plan_row.billing_cycle = None
        plan_row.price_usd_monthly = 0
        from app.billing.plans import FREE_TIER_CREDITS_PER_MONTH
        plan_row.credits_per_month = FREE_TIER_CREDITS_PER_MONTH
        plan_row.canceled_at = datetime.now(timezone.utc)
        plan_row.cancel_at_period_end = False
        plan_row.updated_at = datetime.now(timezone.utc)
        # Clear the active subscription pointer on the mirror.
        sub_row = await db.get(StripeSubscription, workspace_id)
        if sub_row is not None:
            sub_row.stripe_subscription_id = None
            sub_row.stripe_price_id = None
            sub_row.status = "canceled"
            sub_row.updated_at = datetime.now(timezone.utc)
        await db.commit()
        log.info(
            "stripe_sub_deleted_demoted_to_free",
            workspace_id=str(workspace_id),
        )


async def on_invoice_paid(event: dict) -> None:
    """`invoice.payment_succeeded` — monthly renewal (or annual upfront).

    For monthly subs: every successful payment triggers a fresh
    monthly reset. For annual subs: only fires once per year, so the
    monthly reset cron handles the 11 intermediate months.

    Idempotency: we dedup on the invoice id by writing it into the
    ledger note and checking before re-granting. (A heavier dedup
    would need a stripe_event_seen table; not worth it in MVP given
    how few of these we get.)"""
    invoice = event["data"]["object"]
    customer_id = invoice.get("customer")
    subscription_id = invoice.get("subscription")
    invoice_id = invoice.get("id")
    if not (customer_id and subscription_id and invoice_id):
        return

    async with get_session() as db:
        workspace_id = await _resolve_workspace_from_customer(db, customer_id)
        if not workspace_id:
            log.warning("stripe_invoice_paid_no_workspace", customer=customer_id)
            return

        plan_row = await db.get(WorkspaceBillingPlan, workspace_id)
        if plan_row is None or plan_row.plan == PLAN_FREE:
            return

        # Idempotency: scan recent ledger for this invoice id in note.
        from app.db.models import CreditLedger
        existing = await db.execute(
            select(CreditLedger.id).where(
                CreditLedger.workspace_id == workspace_id,
                CreditLedger.kind == "monthly_reset",
                CreditLedger.note.like(f"%{invoice_id}%"),
            )
        )
        if existing.scalar_one_or_none() is not None:
            log.info("stripe_invoice_paid_already_processed", invoice=invoice_id)
            return

        await billing_repo.credit_monthly_reset(
            db,
            workspace_id=workspace_id,
            credits_to_grant=float(plan_row.credits_per_month),
            note=f"Stripe invoice {invoice_id}",
        )
        await db.commit()
        log.info(
            "stripe_invoice_paid_credits_reset",
            workspace_id=str(workspace_id),
            invoice=invoice_id,
            credits=float(plan_row.credits_per_month),
        )


async def on_invoice_failed(event: dict) -> None:
    """`invoice.payment_failed` — payment didn't go through.

    For MVP we just log + warn. Slack DM to the workspace admin is the
    natural follow-up; Stripe will mark the sub `past_due` after its
    retry schedule expires, and subscription.updated will pick that up.

    We don't demote the plan here -- Stripe handles the dunning
    schedule and eventually cancels (triggering subscription.deleted),
    at which point we demote. Premature demotion would punish customers
    in the middle of a transient bank decline."""
    invoice = event["data"]["object"]
    customer_id = invoice.get("customer")
    log.warning(
        "stripe_invoice_payment_failed",
        customer=customer_id,
        invoice=invoice.get("id"),
        amount_due=invoice.get("amount_due"),
    )
    # TODO(slice 3-or-4): post a Slack DM to the workspace admin with
    # an "Update payment method" link. Requires resolving the admin
    # user, which depends on RBAC work that's still a stub.


def _monthly_price_for_plan(plan: str) -> float:
    """The slider's effective monthly price for a given plan. For MVP
    we always use the tier's floor (the price the customer paid in
    Checkout); slider position adjustments happen via Stripe's
    `quantity` and will be wired in Slice 4 when the /settings/billing
    UI lets the customer change it."""
    spec = PLANS.get(plan)
    if spec is None:
        return 0.0
    return spec.price_floor


# Public dispatch map. Keys must match Stripe's event.type strings.
HANDLERS = {
    "checkout.session.completed": on_checkout_completed,
    "customer.subscription.created": on_subscription_created,
    "customer.subscription.updated": on_subscription_updated,
    "customer.subscription.deleted": on_subscription_deleted,
    "invoice.payment_succeeded": on_invoice_paid,
    "invoice.payment_failed": on_invoice_failed,
}


async def dispatch(event: dict) -> bool:
    """Route `event` to the matching handler. Returns True when an
    event handler was found + ran (regardless of side-effects), False
    when the event type isn't in HANDLERS. The webhook endpoint
    converts both into a 200 (Stripe needs that or it retries)."""
    handler = HANDLERS.get(event.get("type", ""))
    if handler is None:
        return False
    await handler(event)
    return True
