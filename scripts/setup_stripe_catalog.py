"""Idempotent setup of the Misterr Stripe catalog (Products + Prices).

What it does:
  1. For each paid plan (Starter, Pro, Scale, Business): finds the
     existing Product by `metadata.misterr_plan == <plan>`, or creates
     it. Updates name + description to match `plans.py` so renaming
     a plan in code propagates on next run.
  2. For each Product: ensures **two** Prices exist (monthly + annual,
     where annual is the 20% discount applied to monthly*12) at the
     `price_floor` of the tier. Slider movement happens at Checkout
     time by adjusting `quantity`, not by creating one price per
     slider position. Old prices are NOT deleted (Stripe doesn't
     allow it), only archived if they no longer match.
  3. Prints a JSON map `<plan>_<cycle> -> price_id` ready to paste into
     `STRIPE_PRICE_IDS_JSON` in Doppler.

Idempotency model: every Product + Price carries a
`metadata.misterr_plan` (and `metadata.misterr_cycle` for prices) so
repeat runs find and update instead of duplicating. Safe to run any
number of times; the only side effect of a re-run when nothing
changed is a no-op API call per object.

Why one price per (plan, cycle) and not one per slider position:
  - Stripe price objects are immutable. A slider with 200 positions
    would mean 200 archived prices accumulated per tier over time.
  - Quantity-based scaling at Checkout achieves the same customer
    UX and keeps the catalog narrow.
  - When we add per-tier base price changes, we just archive the old
    pair and create a new one; existing subscriptions stay on their
    historical price.

Usage:
  doppler run -p sebitas -c dev -- uv run python scripts/setup_stripe_catalog.py
"""

from __future__ import annotations

import json
import sys

import stripe

from app.billing.plans import (
    ANNUAL_DISCOUNT,
    PLAN_BUSINESS,
    PLAN_PRO,
    PLAN_SCALE,
    PLAN_STARTER,
    PLANS,
)
from app.config import get_settings


_PAID_PLANS = (PLAN_STARTER, PLAN_PRO, PLAN_SCALE, PLAN_BUSINESS)


def _find_product(plan: str) -> stripe.Product | None:
    """Find an existing Product tagged with `misterr_plan = <plan>`.
    Uses `search` (Stripe's metadata index) which is eventually
    consistent but fast for catalog-size data."""
    res = stripe.Product.search(query=f"metadata['misterr_plan']:'{plan}'")
    for product in res.auto_paging_iter():
        return product
    return None


def _ensure_product(plan: str) -> stripe.Product:
    spec = PLANS[plan]
    existing = _find_product(plan)
    description = spec.description[:500]  # Stripe limit
    if existing is None:
        return stripe.Product.create(
            name=f"Misterr {spec.display_name}",
            description=description,
            metadata={"misterr_plan": plan},
        )
    # Patch name + description if drifted.
    if existing.name != f"Misterr {spec.display_name}" or existing.description != description:
        return stripe.Product.modify(
            existing.id,
            name=f"Misterr {spec.display_name}",
            description=description,
        )
    return existing


def _find_price(product_id: str, plan: str, cycle: str) -> stripe.Price | None:
    """Find an existing active Price for this (plan, cycle)."""
    res = stripe.Price.search(
        query=(
            f"product:'{product_id}' AND "
            f"metadata['misterr_plan']:'{plan}' AND "
            f"metadata['misterr_cycle']:'{cycle}' AND "
            f"active:'true'"
        )
    )
    for price in res.auto_paging_iter():
        return price
    return None


def _ensure_price(product_id: str, plan: str, cycle: str, amount_usd: float) -> stripe.Price:
    """Ensures an active Price at `amount_usd` for (plan, cycle) exists.

    If a price already exists at that amount, returns it. If a price
    exists at a different amount, archives it and creates a new one
    at the target amount. New customers always land on the latest
    price; existing subscribers stay on whatever historical price
    they signed up at."""
    interval = "month" if cycle == "monthly" else "year"
    unit_amount = int(round(amount_usd * 100))  # Stripe expects cents

    existing = _find_price(product_id, plan, cycle)
    if existing is not None and existing.unit_amount == unit_amount and existing.recurring.interval == interval:
        return existing

    if existing is not None:
        stripe.Price.modify(existing.id, active=False)

    return stripe.Price.create(
        product=product_id,
        currency="usd",
        unit_amount=unit_amount,
        recurring={"interval": interval},
        metadata={"misterr_plan": plan, "misterr_cycle": cycle},
        nickname=f"{plan}-{cycle}",
    )


def main() -> int:
    settings = get_settings()
    if not settings.stripe_api_key:
        print("STRIPE_API_KEY is not set in Doppler. Aborting.", file=sys.stderr)
        return 2
    stripe.api_key = settings.stripe_api_key

    price_ids: dict[str, str] = {}

    for plan in _PAID_PLANS:
        spec = PLANS[plan]
        product = _ensure_product(plan)
        monthly = _ensure_price(product.id, plan, "monthly", spec.price_floor)
        annual_total = spec.price_floor * 12 * (1 - ANNUAL_DISCOUNT)
        annual = _ensure_price(product.id, plan, "annual", annual_total)
        price_ids[f"{plan}_monthly"] = monthly.id
        price_ids[f"{plan}_annual"] = annual.id
        print(
            f"  {plan}: monthly={monthly.id} (${spec.price_floor:.2f}/mo) "
            f"annual={annual.id} (${annual_total:.2f}/yr)"
        )

    print()
    print("STRIPE_PRICE_IDS_JSON (paste into Doppler):")
    print(json.dumps(price_ids, sort_keys=True))
    print()
    print("Doppler command:")
    print(
        "  doppler secrets set STRIPE_PRICE_IDS_JSON='" + json.dumps(price_ids, sort_keys=True) + "' "
        "--project sebitas --config dev --no-interactive"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
