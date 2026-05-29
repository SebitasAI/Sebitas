"""Incoming connect-complete webhooks from integration providers.

Two endpoints share this module:
  POST /integrations/pipedream/webhook    -- Pipedream Connect.
  POST /integrations/composio/webhook     -- Composio Auth.

Each handler verifies its provider's signature (best-effort), dedupes by
event id, parses the event into (workspace_id, app, account_id), and
spawns `connect.complete(...)` out of band so we respond 2xx fast.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import structlog
from fastapi import APIRouter, Request, Response

from app.config import get_settings
from app.integrations import connect

log = structlog.get_logger(__name__)
router = APIRouter()
# Process-local dedupe sets keyed by provider. Production-scale dedupe should
# live in Postgres (same pattern as slack_event_seen); fine for now given
# the small webhook volume and short event TTL.
_seen_events: set[str] = set()
_seen_composio_events: set[str] = set()


def _verify(headers, raw: bytes) -> bool:
    secret = get_settings().pipedream_webhook_secret
    if not secret:
        # No secret configured -> accept (will harden once the exact Pipedream
        # signature scheme is confirmed). Polling fallback still covers us.
        return True
    sig = headers.get("x-pd-signature") or headers.get("x-pipedream-signature")
    if not sig:
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


@router.post("/integrations/pipedream/webhook")
async def pipedream_webhook(request: Request):
    raw = await request.body()
    if not _verify(request.headers, raw):
        return Response(status_code=401)
    try:
        payload = json.loads(raw or b"{}")
    except Exception:  # noqa: BLE001
        return Response(status_code=400)

    event_id = payload.get("id") or payload.get("event_id") or payload.get("timestamp")
    if event_id and event_id in _seen_events:
        return {"ok": True}
    if event_id:
        _seen_events.add(event_id)

    # Defensive extraction: connect-event shapes vary slightly between accounts.
    # The 2025+ Pipedream payload has top-level keys: event, connect_token,
    # environment, connect_session_id, account. The account object uses
    # `external_id` (not `external_user_id`) for the tenant ref. We accept
    # both keys + several legacy fallbacks so older Pipedream tenants still
    # work.
    account = payload.get("account") or payload.get("connect_account") or payload.get("data") or {}
    external_user_id = (
        payload.get("external_user_id")
        or payload.get("external_id")
        or account.get("external_id")            # ← Pipedream's actual field
        or account.get("external_user_id")       # legacy
    )
    app_obj = account.get("app") or {}
    app = app_obj.get("name_slug") or app_obj.get("name") or payload.get("app")
    account_id = account.get("id") or payload.get("account_id")

    if external_user_id and app:
        # Respond fast; perform the resume out of band.
        asyncio.create_task(connect.complete(external_user_id, app, account_id))
    else:
        log.warning(
            "webhook_unparsed",
            keys=list(payload.keys())[:8],
            account_keys=list(account.keys())[:8] if isinstance(account, dict) else None,
            event_type=payload.get("event"),
        )
    return {"ok": True}


def _verify_composio(headers, raw: bytes) -> bool:
    """Composio signs webhooks with an HMAC-SHA256 of the raw body, key from
    the Auth Configs UI. If the secret isn't configured we accept (the
    polling fallback in connect._poll covers correctness)."""
    secret = get_settings().composio_webhook_secret
    if not secret:
        return True
    sig = headers.get("x-composio-signature") or headers.get("x-signature")
    if not sig:
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


@router.post("/integrations/composio/webhook")
async def composio_webhook(request: Request):
    """Composio's connect-complete event. Payload shape (per their docs):
    {
      "type": "connection.created" | "connection.updated" | ...,
      "data": {
        "id": "<connection_id>",
        "user_id": "<our external_user_id, == workspace_id>",
        "toolkit_slug": "<app slug, e.g. 'metabase'>",
        "status": "ACTIVE" | "EXPIRED" | "INACTIVE"
      },
      "timestamp": "...",
      "event_id": "..."
    }
    We resume the paused run only on ACTIVE status; other statuses are
    logged so we can debug + skipped."""
    raw = await request.body()
    if not _verify_composio(request.headers, raw):
        return Response(status_code=401)
    try:
        payload = json.loads(raw or b"{}")
    except Exception:  # noqa: BLE001
        return Response(status_code=400)

    event_id = payload.get("event_id") or payload.get("id") or payload.get("timestamp")
    if event_id and event_id in _seen_composio_events:
        return {"ok": True}
    if event_id:
        _seen_composio_events.add(event_id)

    data = payload.get("data") or {}
    user_id = data.get("user_id") or data.get("entity_id") or payload.get("user_id")
    toolkit = data.get("toolkit_slug") or data.get("app_slug") or payload.get("toolkit_slug")
    account_id = data.get("id") or data.get("connection_id")
    status = (data.get("status") or "").upper()
    event_type = payload.get("type") or payload.get("event")

    if status and status != "ACTIVE":
        log.info(
            "composio_webhook_non_active",
            event_type=event_type, status=status, app=toolkit,
        )
        return {"ok": True}

    if user_id and toolkit:
        asyncio.create_task(connect.complete(user_id, toolkit, account_id))
    else:
        log.warning(
            "composio_webhook_unparsed",
            keys=list(payload.keys())[:8],
            data_keys=list(data.keys())[:8] if isinstance(data, dict) else None,
            event_type=event_type,
        )
    return {"ok": True}
