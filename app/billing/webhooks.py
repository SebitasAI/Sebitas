"""Stripe webhook endpoint. One URL, signed payloads only.

`/webhooks/stripe`:
  - Reads the raw body (signature is over bytes, not parsed JSON)
  - Verifies the `stripe-signature` header against the workspace
    signing secret (`STRIPE_WEBHOOK_SECRET`)
  - Dispatches to `webhook_handlers.dispatch` for the 6 supported
    events; unknown event types are acked with 200 so Stripe doesn't
    retry them
  - On signature failure: 400 (Stripe stops retrying)
  - On unconfigured billing: 503 (Stripe retries; acceptable, the dev
    workspace has billing off intentionally and ops will fix the env)

Stripe's contract: a non-2xx response triggers automatic retries with
exponential backoff for 3 days. Returning 200 even on handler errors
is intentional -- a buggy handler shouldn't queue infinite retries.
The handler logs the error, we fix it, and Stripe state stays
authoritative."""

from __future__ import annotations

import asyncio

import stripe
import structlog
from fastapi import APIRouter, Header, Request, Response

from app.billing import stripe_client, webhook_handlers

log = structlog.get_logger(__name__)

router = APIRouter()


@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="stripe-signature"),
) -> Response:
    """Handle a single Stripe event delivery."""
    if not stripe_client.is_configured():
        # Billing not wired up for this env. 503 makes Stripe retry,
        # which is what we want during a partial rollout (handler
        # ships before env vars are set on prod, etc).
        log.warning("stripe_webhook_received_but_billing_unconfigured")
        return Response(status_code=503, content="billing not configured")

    if not stripe_signature:
        return Response(status_code=400, content="missing stripe-signature header")

    payload = await request.body()
    try:
        event = stripe_client.verify_webhook_signature(
            payload=payload, signature_header=stripe_signature
        )
    except stripe.SignatureVerificationError:
        # Tampered or replayed. Don't retry.
        log.warning("stripe_webhook_signature_invalid")
        return Response(status_code=400, content="invalid signature")
    except Exception as exc:  # noqa: BLE001
        log.error("stripe_webhook_verify_errored", error=str(exc)[:200])
        return Response(status_code=400, content="verification failed")

    event_type = event.get("type", "")
    event_id = event.get("id", "")

    # Dispatch in a background task so we ack Stripe quickly even when
    # the handler is slow (DB lock contention, etc). Stripe's signing
    # secret already guaranteed authenticity, so it's safe to detach.
    async def _run() -> None:
        try:
            handled = await webhook_handlers.dispatch(event)
            if not handled:
                log.info("stripe_webhook_unhandled_event", type=event_type, id=event_id)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "stripe_webhook_handler_errored",
                type=event_type, id=event_id,
                error=str(exc)[:300],
            )

    asyncio.create_task(_run())
    return Response(status_code=200, content="ok")
