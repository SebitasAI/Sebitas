"""REST endpoints powering the Misterr web `/settings/billing` page.

Three endpoints, all Clerk-authenticated + workspace-scoped via
`require_app_user`:

  GET  /api/billing/overview   -> current plan + balance + ledger tail
  POST /api/billing/checkout   -> Stripe Checkout URL for a (plan, cycle)
  POST /api/billing/portal     -> Stripe Customer Portal URL

All Stripe calls go through `app.billing.stripe_client`. When Stripe
isn't configured (Doppler env empty), every paid-flow endpoint returns
503 so the UI can render "billing unavailable" instead of a crash.

Why all in one file: these three routes share request shapes, auth
plumbing, and the customer-resolution helper. Splitting into per-route
modules would buy noise.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select

from app.auth.clerk import ResolvedAppUser, require_app_user
from app.auth.rbac import require_workspace_admin
from app.billing import stripe_client
from app.billing.plans import (
    ALL_PLANS,
    BYPASS_CREDIT_CHECK,
    FREE_TIER_CREDITS_PER_MONTH,
    PLAN_FREE,
    PLAN_UNLIMITED,
    PLANS,
    annual_price,
)
from app.config import get_settings
from app.db.models import (
    AppUser,
    CreditBalance,
    CreditLedger,
    StripeSubscription,
    Workspace,
    WorkspaceBillingPlan,
)
from app.db.session import get_session

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/billing", tags=["billing"])


# --------------------------------------------------------------------------- #
# Response schemas
# --------------------------------------------------------------------------- #


class PlanOption(BaseModel):
    name: str
    display_name: str
    monthly_price_floor: float
    monthly_price_ceiling: float
    credits_floor: int
    credits_ceiling: int
    description: str
    annual_price_floor: float  # 12 months @ floor, 20% off
    has_monthly_checkout: bool
    has_annual_checkout: bool


class OverviewResponse(BaseModel):
    plan: str
    plan_display_name: str
    billing_cycle: Literal["monthly", "annual"] | None
    credits_per_month: float
    balance_credits: float
    price_usd_monthly: float
    current_period_start: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    is_unlimited: bool
    has_active_subscription: bool
    available_plans: list[PlanOption]
    stripe_configured: bool


class LedgerEntry(BaseModel):
    id: str
    delta_credits: float
    kind: str
    balance_after_credits: float
    note: str | None
    created_at: datetime


class CheckoutRequest(BaseModel):
    plan: str
    cycle: Literal["monthly", "annual"]


class CheckoutResponse(BaseModel):
    url: str


class PortalResponse(BaseModel):
    url: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _build_plan_options() -> list[PlanOption]:
    """Plans the UI offers as upgrade targets. Excludes the sentinels
    (free, enterprise, unlimited) -- free is the default, enterprise is
    sales-led, unlimited is internal."""
    price_ids = stripe_client.get_price_ids()
    out: list[PlanOption] = []
    for name in ("starter", "pro", "scale", "business"):
        spec = PLANS[name]
        out.append(
            PlanOption(
                name=name,
                display_name=spec.display_name,
                monthly_price_floor=spec.price_floor,
                monthly_price_ceiling=spec.price_ceiling,
                credits_floor=spec.credits_floor,
                credits_ceiling=spec.credits_ceiling,
                description=spec.description,
                annual_price_floor=annual_price(spec.price_floor),
                has_monthly_checkout=f"{name}_monthly" in price_ids,
                has_annual_checkout=f"{name}_annual" in price_ids,
            )
        )
    return out


async def _resolve_stripe_customer(workspace_id) -> str | None:
    async with get_session() as session:
        row = await session.get(StripeSubscription, workspace_id)
        return row.stripe_customer_id if row else None


async def _ensure_stripe_customer(
    workspace_id, *, email: str | None, workspace_name: str | None
) -> str:
    """Find or create the Stripe Customer for this workspace. The
    `stripe_subscription` row is the local mirror of the customer
    binding; we create / update it as we go."""
    existing = await _resolve_stripe_customer(workspace_id)
    if existing:
        return existing

    customer_id = await stripe_client.create_customer(
        workspace_id=str(workspace_id),
        email=email,
        name=workspace_name,
    )
    async with get_session() as session:
        row = await session.get(StripeSubscription, workspace_id)
        if row is None:
            session.add(
                StripeSubscription(
                    workspace_id=workspace_id,
                    stripe_customer_id=customer_id,
                )
            )
        else:
            row.stripe_customer_id = customer_id
            row.updated_at = datetime.now(timezone.utc)
        await session.commit()
    return customer_id


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get("/overview", response_model=OverviewResponse)
async def overview(
    me: ResolvedAppUser = Depends(require_app_user),
) -> OverviewResponse:
    """Plan + balance + period info + plan ladder for the upgrade UI."""
    async with get_session() as session:
        plan_row = await session.get(WorkspaceBillingPlan, me.workspace_id)
        balance_row = await session.get(CreditBalance, me.workspace_id)
        sub_row = await session.get(StripeSubscription, me.workspace_id)

    if plan_row is None:
        # New workspace pre-bootstrap. Tell the UI 'free' so the page
        # renders the free state instead of erroring.
        plan_name = PLAN_FREE
        plan_display = "Free"
        cycle = None
        credits_per_month = FREE_TIER_CREDITS_PER_MONTH
        price_monthly = 0.0
        period_start = None
        period_end = None
        cancel_flag = False
    else:
        plan_name = plan_row.plan
        plan_display = PLANS.get(plan_name).display_name if plan_name in PLANS else plan_name.title()
        cycle = plan_row.billing_cycle if plan_row.billing_cycle in ("monthly", "annual") else None
        credits_per_month = float(plan_row.credits_per_month)
        price_monthly = float(plan_row.price_usd_monthly)
        period_start = plan_row.current_period_start
        period_end = plan_row.current_period_end
        cancel_flag = bool(plan_row.cancel_at_period_end)

    balance_credits = float(balance_row.balance_credits) if balance_row else 0.0

    return OverviewResponse(
        plan=plan_name,
        plan_display_name=plan_display,
        billing_cycle=cycle,
        credits_per_month=credits_per_month,
        balance_credits=balance_credits,
        price_usd_monthly=price_monthly,
        current_period_start=period_start,
        current_period_end=period_end,
        cancel_at_period_end=cancel_flag,
        is_unlimited=plan_name == PLAN_UNLIMITED,
        has_active_subscription=bool(
            sub_row and sub_row.stripe_subscription_id and sub_row.status == "active"
        ),
        available_plans=_build_plan_options(),
        stripe_configured=stripe_client.is_configured(),
    )


@router.get("/ledger", response_model=list[LedgerEntry])
async def ledger(
    me: ResolvedAppUser = Depends(require_app_user),
    limit: int = 30,
) -> list[LedgerEntry]:
    """Recent credit-balance changes for the audit panel of the
    settings page. Newest first. Limit is hard-capped at 100 to keep
    the response cheap."""
    limit = max(1, min(limit, 100))
    async with get_session() as session:
        rows = (
            await session.execute(
                select(CreditLedger)
                .where(CreditLedger.workspace_id == me.workspace_id)
                .order_by(desc(CreditLedger.created_at))
                .limit(limit)
            )
        ).scalars().all()
    return [
        LedgerEntry(
            id=str(r.id),
            delta_credits=float(r.delta_credits),
            kind=r.kind,
            balance_after_credits=float(r.balance_after_credits),
            note=r.note,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    body: CheckoutRequest,
    me: ResolvedAppUser = Depends(require_app_user),
    _: object = Depends(require_workspace_admin),
) -> CheckoutResponse:
    """Start a Stripe Checkout session for the requested (plan, cycle).
    Returns the URL the browser should redirect to.

    Admin-only: only workspace admins (org:admin) can change the plan."""
    if not stripe_client.is_configured():
        raise HTTPException(503, detail="Billing is not configured for this environment")

    if body.plan not in PLANS or body.plan in BYPASS_CREDIT_CHECK or body.plan == PLAN_FREE:
        # Don't let the UI ask for a tier that isn't a Stripe-flow.
        raise HTTPException(400, detail=f"Plan '{body.plan}' is not purchasable")

    price_ids = stripe_client.get_price_ids()
    key = f"{body.plan}_{body.cycle}"
    price_id = price_ids.get(key)
    if not price_id:
        raise HTTPException(
            503,
            detail=(
                f"No Stripe price configured for {key}. "
                "Run scripts/setup_stripe_catalog.py and reload "
                "STRIPE_PRICE_IDS_JSON in Doppler."
            ),
        )

    # Look up the workspace for the email / name passed to Stripe.
    async with get_session() as session:
        workspace = await session.get(Workspace, me.workspace_id)
        workspace_name = workspace.name if workspace else None

    customer_id = await _ensure_stripe_customer(
        me.workspace_id, email=me.email, workspace_name=workspace_name
    )

    base = get_settings().web_base_url.rstrip("/")
    url = await stripe_client.create_checkout_session(
        customer_id=customer_id,
        price_id=price_id,
        success_url=f"{base}/settings/billing?checkout=success",
        cancel_url=f"{base}/settings/billing?checkout=cancel",
        workspace_id=str(me.workspace_id),
        plan=body.plan,
        cycle=body.cycle,
    )
    return CheckoutResponse(url=url)


@router.post("/portal", response_model=PortalResponse)
async def create_portal(
    me: ResolvedAppUser = Depends(require_app_user),
    _: object = Depends(require_workspace_admin),
) -> PortalResponse:
    """Return a Stripe Customer Portal URL for the current workspace.
    The portal is where the customer manages payment methods, sees
    invoices, and cancels. Requires an already-existing Stripe Customer
    (you can't 'manage' a subscription you don't have).

    Admin-only: changing payment methods, cancelling subs, downloading
    invoices is restricted to org admins."""
    if not stripe_client.is_configured():
        raise HTTPException(503, detail="Billing is not configured for this environment")

    customer_id = await _resolve_stripe_customer(me.workspace_id)
    if not customer_id:
        raise HTTPException(
            400,
            detail=(
                "No active Stripe Customer for this workspace. "
                "Upgrade to a paid plan first to access the Customer Portal."
            ),
        )

    base = get_settings().web_base_url.rstrip("/")
    url = await stripe_client.create_billing_portal_session(
        customer_id=customer_id,
        return_url=f"{base}/settings/billing",
    )
    return PortalResponse(url=url)
