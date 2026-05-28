"""Incoming Pipedream Connect webhook. Verifies signature (best-effort), dedupes
by event id, responds 2xx fast, and spawns the resume out of band."""

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
_seen_events: set[str] = set()


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
