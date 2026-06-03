"""End-to-end smoke test for the billing system, runnable against
Stripe test mode + the dev Neon database.

What this script does (in order):

  1. Create a throwaway Workspace + AppUser in the dev DB
  2. Hit the four lifecycle paths via the webhook dispatcher:
     a. checkout.session.completed   ->  plan = starter, +100k credits
     b. invoice.payment_succeeded    ->  monthly reset (no double grant
                                         because the dedup-by-invoice
                                         path sees the prior reset)
     c. customer.subscription.updated to plan=pro  -> +400k credits, plan flips
     d. customer.subscription.deleted -> plan demotes to free
  3. Verify after every step: row state in workspace_billing_plan,
     credit_balance, credit_ledger.
  4. Run the pre-flight check after draining balance to zero and assert
     it correctly blocks the run.
  5. Clean up: delete the throwaway workspace (CASCADEs every row).

What it does NOT cover (and why):

  - Real Checkout / Customer Portal HTTP flows. Those require a logged-
    in browser session against the dev backend, which I can't drive
    headless from this script. See the manual checklist printed at the
    end of the run.
  - The actual `/webhooks/stripe` HTTP endpoint. The dispatcher we call
    directly runs the same code path, so the only thing this script
    skips is the signature-verification layer (which has its own unit
    tests).
  - The monthly_reset cron loop. Tested separately in test_monthly_reset.py.

Usage:
  doppler run -p sebitas -c dev -- uv run python scripts/smoke_billing.py

Exit code: 0 on success, 1 on assertion failure. Run from a clean shell
so the cleanup at the end leaves the dev DB the way it was.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Repo root on sys.path so `app.*` imports work when invoked as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stripe  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app.billing import webhook_handlers  # noqa: E402
from app.billing import repository as billing_repo  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.models import (  # noqa: E402
    AppUser,
    CreditBalance,
    CreditLedger,
    StripeSubscription,
    Workspace,
    WorkspaceBillingPlan,
)
from app.db.session import get_session  # noqa: E402


# Colored output for readability when run interactively.
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"


def _print_step(label: str) -> None:
    print(f"\n{_YELLOW}=== {label} ==={_RESET}")


def _ok(msg: str) -> None:
    print(f"{_GREEN}✓{_RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"{_RED}✗ {msg}{_RESET}")
    raise AssertionError(msg)


def _ts() -> int:
    """Current UTC as a UNIX timestamp (Stripe-style)."""
    return int(datetime.now(timezone.utc).timestamp())


async def _make_workspace() -> tuple[uuid.UUID, str]:
    """Insert a throwaway workspace + plan + balance row so the
    handlers have something to attach to."""
    async with get_session() as session:
        team_id = f"T{uuid.uuid4().hex[:10].upper()}SMOKE"
        ws = Workspace(slack_team_id=team_id, name="Billing Smoke Test")
        session.add(ws)
        await session.flush()
        session.add(
            WorkspaceBillingPlan(
                workspace_id=ws.id,
                plan="free",
                credits_per_month=50_000,
                price_usd_monthly=0,
            )
        )
        session.add(CreditBalance(workspace_id=ws.id, balance_credits=50_000))
        await session.commit()
        return ws.id, team_id


async def _cleanup_workspace(workspace_id: uuid.UUID) -> None:
    async with get_session() as session:
        # Explicit deletes in dependency order so we don't rely on CASCADE
        # behavior for the (no-FK) ledger references.
        await session.execute(
            delete(CreditLedger).where(CreditLedger.workspace_id == workspace_id)
        )
        await session.execute(
            delete(StripeSubscription).where(
                StripeSubscription.workspace_id == workspace_id
            )
        )
        await session.execute(
            delete(CreditBalance).where(CreditBalance.workspace_id == workspace_id)
        )
        await session.execute(
            delete(WorkspaceBillingPlan).where(
                WorkspaceBillingPlan.workspace_id == workspace_id
            )
        )
        await session.execute(
            delete(Workspace).where(Workspace.id == workspace_id)
        )
        await session.commit()


async def _get_state(workspace_id: uuid.UUID) -> dict[str, Any]:
    """Snapshot of the four key billing rows for this workspace."""
    async with get_session() as session:
        plan = await session.get(WorkspaceBillingPlan, workspace_id)
        balance = await session.get(CreditBalance, workspace_id)
        sub = await session.get(StripeSubscription, workspace_id)
        ledger_count = (
            await session.execute(
                select(CreditLedger).where(CreditLedger.workspace_id == workspace_id)
            )
        ).scalars().all()
    return {
        "plan": plan.plan if plan else None,
        "cycle": plan.billing_cycle if plan else None,
        "credits_per_month": float(plan.credits_per_month) if plan else None,
        "balance": float(balance.balance_credits) if balance else None,
        "sub_id": sub.stripe_subscription_id if sub else None,
        "sub_status": sub.status if sub else None,
        "ledger_count": len(ledger_count),
    }


def _make_subscription_event_obj(
    *,
    customer_id: str,
    subscription_id: str,
    workspace_id: uuid.UUID,
    plan: str,
    cycle: str,
    price_id: str,
    status: str = "active",
) -> dict:
    """Synthesize a Stripe event.data.object for subscription events.
    Shape mirrors what real Stripe emits closely enough for the
    handler -- the fields the dispatcher actually reads."""
    now = _ts()
    period_end = now + 30 * 24 * 60 * 60
    return {
        "id": subscription_id,
        "customer": customer_id,
        "status": status,
        "current_period_start": now,
        "current_period_end": period_end,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": price_id}}]},
        "metadata": {
            "workspace_id": str(workspace_id),
            "plan": plan,
            "cycle": cycle,
        },
    }


async def _stripe_lookup_test_objects() -> tuple[str, str, str]:
    """Reach Stripe to grab a real (customer, starter_monthly_price_id,
    pro_monthly_price_id) so the script tests against actual catalog
    state, not hardcoded ids."""
    s = get_settings()
    if not s.stripe_api_key:
        _fail("STRIPE_API_KEY is not set. Run via `doppler run`.")
    stripe.api_key = s.stripe_api_key

    # Create a fresh test customer for this run.
    customer = await asyncio.to_thread(
        stripe.Customer.create,
        email="smoke-test@example.com",
        name="Misterr Smoke Test",
        metadata={"smoke": "1"},
    )
    _ok(f"Stripe Customer created: {customer.id}")

    # Resolve price ids from the env (set by setup_stripe_catalog.py).
    from app.billing import stripe_client
    price_ids = stripe_client.get_price_ids()
    starter_id = price_ids.get("starter_monthly")
    pro_id = price_ids.get("pro_monthly")
    if not starter_id or not pro_id:
        _fail(
            "STRIPE_PRICE_IDS_JSON missing starter_monthly / pro_monthly. "
            "Run scripts/setup_stripe_catalog.py first."
        )
    return customer.id, starter_id, pro_id


async def _stripe_cleanup(customer_id: str) -> None:
    try:
        await asyncio.to_thread(stripe.Customer.delete, customer_id)
        _ok(f"Stripe Customer deleted: {customer_id}")
    except Exception as exc:  # noqa: BLE001
        print(f"{_YELLOW}! customer cleanup failed: {exc}{_RESET}")


async def run() -> int:
    workspace_id: uuid.UUID | None = None
    customer_id: str | None = None
    try:
        _print_step("Setup: workspace + Stripe customer")
        workspace_id, team_id = await _make_workspace()
        _ok(f"Workspace inserted: {workspace_id} (team={team_id})")
        customer_id, starter_price, pro_price = await _stripe_lookup_test_objects()

        # Initial state check.
        s0 = await _get_state(workspace_id)
        assert s0["plan"] == "free", f"initial plan should be free, got {s0['plan']}"
        assert s0["balance"] == 50_000, f"initial balance 50k, got {s0['balance']}"
        _ok(f"Initial state: plan={s0['plan']} balance={int(s0['balance'])}")

        # --- Step 1: checkout.session.completed --------------------------- #
        _print_step("Step 1: checkout.session.completed -> Starter Monthly")
        subscription_id = f"sub_smoke_{uuid.uuid4().hex[:16]}"

        # The handler calls stripe_client.retrieve_subscription to fetch the
        # canonical sub state. Skip the real fetch: we'd need to actually
        # create the sub in Stripe, which is more brittle. Stub the lookup.
        from app.billing import stripe_client as _sc
        original_retrieve = _sc.retrieve_subscription

        async def _stub_retrieve(_sub_id: str):
            return type("Sub", (), {
                "id": subscription_id,
                "status": "active",
                "current_period_start": _ts(),
                "current_period_end": _ts() + 30 * 24 * 3600,
                "cancel_at_period_end": False,
                "items": type("Items", (), {
                    "data": [type("Item", (), {
                        "price": type("P", (), {"id": starter_price})()
                    })()]
                })(),
            })()

        _sc.retrieve_subscription = _stub_retrieve  # type: ignore[assignment]
        try:
            event = {
                "id": f"evt_smoke_{uuid.uuid4().hex[:16]}",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "customer": customer_id,
                        "subscription": subscription_id,
                        "metadata": {
                            "workspace_id": str(workspace_id),
                            "plan": "starter",
                            "cycle": "monthly",
                        },
                    }
                },
            }
            await webhook_handlers.dispatch(event)
        finally:
            _sc.retrieve_subscription = original_retrieve  # type: ignore[assignment]

        s1 = await _get_state(workspace_id)
        assert s1["plan"] == "starter", f"plan should be starter, got {s1['plan']}"
        assert s1["cycle"] == "monthly", f"cycle should be monthly, got {s1['cycle']}"
        assert s1["balance"] == 100_000, f"balance should be 100k, got {s1['balance']}"
        assert s1["sub_id"] == subscription_id, "subscription_id mismatch"
        assert s1["sub_status"] == "active", "sub status should be active"
        _ok(
            f"After checkout: plan={s1['plan']} balance={int(s1['balance'])} "
            f"sub={s1['sub_id']}"
        )

        # --- Step 2: invoice.payment_succeeded ---------------------------- #
        _print_step("Step 2: invoice.payment_succeeded (renewal grants credits)")
        # Burn 30k credits first so we can see the reset bring balance back up.
        async with get_session() as session:
            await billing_repo.debit_for_agent_run(
                session,
                workspace_id=workspace_id,
                agent_run_id=uuid.uuid4(),
                sales_cost_usd=30.0,
                credits_to_debit=30_000,
            )
            await session.commit()
        s1b = await _get_state(workspace_id)
        assert s1b["balance"] == 70_000, f"after burn 70k, got {s1b['balance']}"
        _ok(f"Burned 30k credits -> balance={int(s1b['balance'])}")

        invoice_id = f"in_smoke_{uuid.uuid4().hex[:16]}"
        await webhook_handlers.dispatch(
            {
                "id": f"evt_smoke_{uuid.uuid4().hex[:16]}",
                "type": "invoice.payment_succeeded",
                "data": {
                    "object": {
                        "id": invoice_id,
                        "customer": customer_id,
                        "subscription": subscription_id,
                    }
                },
            }
        )
        s2 = await _get_state(workspace_id)
        assert s2["balance"] == 100_000, f"reset to 100k, got {s2['balance']}"
        _ok(f"After invoice paid: balance reset to {int(s2['balance'])}")

        # Idempotency: same invoice fired again must NOT double-grant.
        await webhook_handlers.dispatch(
            {
                "id": f"evt_smoke_{uuid.uuid4().hex[:16]}",
                "type": "invoice.payment_succeeded",
                "data": {
                    "object": {
                        "id": invoice_id,
                        "customer": customer_id,
                        "subscription": subscription_id,
                    }
                },
            }
        )
        s2b = await _get_state(workspace_id)
        assert s2b["balance"] == 100_000, (
            f"idempotency broken: balance changed to {s2b['balance']}"
        )
        _ok("Idempotency confirmed: replay of same invoice is a no-op")

        # --- Step 3: subscription.updated -> upgrade to Pro --------------- #
        _print_step("Step 3: customer.subscription.updated -> upgrade to Pro")
        await webhook_handlers.dispatch(
            {
                "id": f"evt_smoke_{uuid.uuid4().hex[:16]}",
                "type": "customer.subscription.updated",
                "data": {
                    "object": _make_subscription_event_obj(
                        customer_id=customer_id,
                        subscription_id=subscription_id,
                        workspace_id=workspace_id,
                        plan="pro",
                        cycle="monthly",
                        price_id=pro_price,
                    )
                },
            }
        )
        s3 = await _get_state(workspace_id)
        assert s3["plan"] == "pro", f"plan should be pro, got {s3['plan']}"
        assert s3["balance"] == 400_000, f"balance should be 400k, got {s3['balance']}"
        _ok(f"After upgrade: plan={s3['plan']} balance={int(s3['balance'])}")

        # --- Step 4: subscription.deleted -> demote to free --------------- #
        _print_step("Step 4: customer.subscription.deleted -> demote to free")
        await webhook_handlers.dispatch(
            {
                "id": f"evt_smoke_{uuid.uuid4().hex[:16]}",
                "type": "customer.subscription.deleted",
                "data": {
                    "object": _make_subscription_event_obj(
                        customer_id=customer_id,
                        subscription_id=subscription_id,
                        workspace_id=workspace_id,
                        plan="pro",
                        cycle="monthly",
                        price_id=pro_price,
                        status="canceled",
                    )
                },
            }
        )
        s4 = await _get_state(workspace_id)
        assert s4["plan"] == "free", f"plan should be free after cancel, got {s4['plan']}"
        # Balance is preserved on cancel; customer can still use leftover credits.
        assert s4["balance"] == 400_000, (
            f"balance preserved on cancel; got {s4['balance']}"
        )
        assert s4["sub_status"] == "canceled", "sub status should be canceled"
        _ok(
            f"After cancel: plan={s4['plan']} (balance preserved {int(s4['balance'])})"
        )

        # --- Step 5: pre-flight check blocks when balance = 0 ------------- #
        _print_step("Step 5: pre-flight blocks runs when balance hits zero")
        async with get_session() as session:
            await billing_repo.debit_for_agent_run(
                session,
                workspace_id=workspace_id,
                agent_run_id=uuid.uuid4(),
                sales_cost_usd=400.0,
                credits_to_debit=400_000,
            )
            await session.commit()
        async with get_session() as session:
            verdict = await billing_repo.preflight_check(session, workspace_id)
        assert verdict.allowed is False, "pre-flight should block at zero"
        assert verdict.reason == "zero_balance", f"reason should be zero_balance, got {verdict.reason}"
        _ok(
            f"Pre-flight verdict: allowed={verdict.allowed} reason={verdict.reason} "
            f"balance={verdict.balance_credits}"
        )

        # --- Step 6: ledger row count makes sense ------------------------- #
        _print_step("Step 6: ledger audit trail")
        sN = await _get_state(workspace_id)
        # Expected: initial_grant on bootstrap + plan-activation grant +
        # debit + monthly-reset + plan-change grant + final debit = 6 rows.
        assert sN["ledger_count"] >= 5, (
            f"expected >=5 ledger rows, got {sN['ledger_count']}"
        )
        _ok(f"Ledger row count: {sN['ledger_count']}")

        print()
        print(f"{_GREEN}ALL ASSERTIONS PASSED{_RESET}")
        print()
        print("Manual checks still needed (browser-only):")
        print("  [ ] Visit /settings/billing while logged in -> renders current plan")
        print("  [ ] Click 'Seleccionar' on Starter -> redirects to Stripe Checkout")
        print("  [ ] Complete checkout with 4242 4242 4242 4242 -> redirects back")
        print("  [ ] 'Administrar' button -> opens Customer Portal")
        print("  [ ] Slack: drain dev workspace to 0, send a message -> hard-stop card")
        return 0

    finally:
        if workspace_id is not None:
            await _cleanup_workspace(workspace_id)
            _ok(f"Workspace cleaned up: {workspace_id}")
        if customer_id is not None:
            await _stripe_cleanup(customer_id)


if __name__ == "__main__":
    try:
        rc = asyncio.run(run())
        sys.exit(rc)
    except AssertionError as exc:
        print(f"\n{_RED}SMOKE FAILED: {exc}{_RESET}")
        sys.exit(1)
