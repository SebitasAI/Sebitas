"""Async wrapper around the stripe-python SDK.

The official `stripe` SDK is sync. We use it from async FastAPI handlers
via `asyncio.to_thread`, which is fine: Stripe calls are I/O-bound and
the request volume is low (one call per webhook, per Checkout, per
subscription update).

Three responsibilities:

  - Provide a single source of truth for "is billing configured?" so
    callers don't have to repeat the `settings.stripe_api_key is None`
    check.

  - Lazily set `stripe.api_key` on the first call so importing this
    module doesn't blow up an environment that hasn't filled the
    Doppler secret yet.

  - Expose the handful of Stripe operations we actually need (create
    customer, create checkout, create billing portal, retrieve
    subscription, retrieve invoice). Anything more exotic lives in
    the call site -- this is a thin wrapper, not a re-implementation.

`BillingNotConfiguredError` is raised by every operation when the API
key is missing so callers can downgrade to a 503 / 501 response in one
predictable place.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import stripe
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)

_api_key_loaded: bool = False


class BillingNotConfiguredError(RuntimeError):
    """Raised when a billing operation runs but `STRIPE_API_KEY` is
    unset. Caller maps this to a 503 / 501 response."""


def is_configured() -> bool:
    """True iff both Stripe secrets are loaded. Webhook endpoint reads
    this to decide between accepting requests vs 503."""
    s = get_settings()
    return bool(s.stripe_api_key and s.stripe_webhook_secret)


def _ensure_api_key() -> None:
    """Lazily install the API key on the stripe SDK. Called by every
    operation here; idempotent."""
    global _api_key_loaded
    if _api_key_loaded:
        return
    s = get_settings()
    if not s.stripe_api_key:
        raise BillingNotConfiguredError(
            "STRIPE_API_KEY not set; cannot reach the Stripe API"
        )
    stripe.api_key = s.stripe_api_key
    _api_key_loaded = True


def get_price_ids() -> dict[str, str]:
    """Map of `<plan>_<cycle>` -> Stripe price_id. Populated by the
    setup script and stored as a JSON string in `STRIPE_PRICE_IDS_JSON`.
    Returns {} when the secret is unset (Checkout will refuse to start
    a paid flow in that case)."""
    raw = get_settings().stripe_price_ids_json
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except json.JSONDecodeError:
        log.warning("stripe_price_ids_json_malformed")
    return {}


async def create_customer(*, workspace_id: str, email: str | None, name: str | None) -> str:
    """Create a Stripe Customer and return its id. `workspace_id` is
    stored on the Customer's `metadata` so webhook handlers can map
    back to the local row without an extra DB lookup."""
    _ensure_api_key()
    params: dict[str, Any] = {"metadata": {"workspace_id": workspace_id}}
    if email:
        params["email"] = email
    if name:
        params["name"] = name
    customer = await asyncio.to_thread(stripe.Customer.create, **params)
    return customer.id


async def create_checkout_session(
    *,
    customer_id: str,
    price_id: str,
    success_url: str,
    cancel_url: str,
    workspace_id: str,
    plan: str,
    cycle: str,
) -> str:
    """Returns the Checkout session URL. Customer is forced (no email
    re-collection); the session metadata carries workspace_id + plan +
    cycle so `checkout.session.completed` can route to the right row.

    `cycle` is 'monthly' | 'annual'; included for analytics + to
    disambiguate when handlers reconcile the subscription against the
    plan ladder."""
    _ensure_api_key()
    session = await asyncio.to_thread(
        stripe.checkout.Session.create,
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "workspace_id": workspace_id,
            "plan": plan,
            "cycle": cycle,
        },
        # Mirror metadata onto the subscription created by this checkout
        # so customer.subscription.* events also carry it. Without this,
        # the only event that knows the plan is checkout.session.completed.
        subscription_data={
            "metadata": {
                "workspace_id": workspace_id,
                "plan": plan,
                "cycle": cycle,
            },
        },
        allow_promotion_codes=True,
    )
    return session.url


async def create_billing_portal_session(*, customer_id: str, return_url: str) -> str:
    """Returns the Customer Portal URL. Customer manages payment method,
    invoices, and cancels here. We don't pass a configuration_id, so the
    portal uses the dashboard's default configuration (which the
    /pricing setup script can also create + pin if we want)."""
    _ensure_api_key()
    session = await asyncio.to_thread(
        stripe.billing_portal.Session.create,
        customer=customer_id,
        return_url=return_url,
    )
    return session.url


async def retrieve_subscription(subscription_id: str) -> stripe.Subscription:
    _ensure_api_key()
    return await asyncio.to_thread(stripe.Subscription.retrieve, subscription_id)


async def retrieve_customer(customer_id: str) -> stripe.Customer:
    _ensure_api_key()
    return await asyncio.to_thread(stripe.Customer.retrieve, customer_id)


async def cancel_subscription(subscription_id: str) -> stripe.Subscription:
    """Cancel at period end (graceful). Customer keeps access through
    `current_period_end`; renewal is suppressed."""
    _ensure_api_key()
    return await asyncio.to_thread(
        stripe.Subscription.modify,
        subscription_id,
        cancel_at_period_end=True,
    )


def verify_webhook_signature(*, payload: bytes, signature_header: str) -> dict:
    """Verify the Stripe signature header against `STRIPE_WEBHOOK_SECRET`
    and return the parsed event dict. Raises `stripe.SignatureVerificationError`
    on tamper / replay; the webhook handler maps that to a 400."""
    s = get_settings()
    if not s.stripe_webhook_secret:
        raise BillingNotConfiguredError(
            "STRIPE_WEBHOOK_SECRET not set; cannot verify webhook"
        )
    event = stripe.Webhook.construct_event(
        payload=payload,
        sig_header=signature_header,
        secret=s.stripe_webhook_secret,
    )
    return event
