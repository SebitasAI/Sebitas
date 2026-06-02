"""Inbound webhook endpoints. Three URLs, three auth schemes:

- `/webhooks/auto/{webhook_secret}`  -- direct: URL-as-secret. Anyone
  with the URL can fire. No headers required.
- `/webhooks/pipedream/{automation_id}` -- Pipedream-routed. HMAC with
  per-trigger signing key stored on the row.
- `/webhooks/composio/{automation_id}` -- Composio-routed. HMAC with
  account-wide secret in Doppler.

All endpoints return 200 OK as fast as possible. The fire itself runs
on a background task so upstream providers (especially Pipedream and
Composio) get the ack within their timeout window. A misbehaving fire
shows up as a `failed` automation_run row, not as a retried webhook
delivery."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Request, Response

from app.automations import repository as repo
from app.automations import router as auto_router
from app.automations import triggers as _triggers
from app.db.session import get_session
from app.slack.crypto import TokenCryptoError, decrypt_token

log = structlog.get_logger(__name__)

router = APIRouter()


async def _parse_body(request: Request) -> tuple[bytes, dict[str, Any]]:
    """Read body once. If JSON, return parsed dict; if anything else,
    wrap raw body in `{"raw_body": str}` so templates can still see
    something. Caller uses `raw` for signature verification."""
    raw = await request.body()
    if not raw:
        return raw, {}
    try:
        import json

        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return raw, parsed
        return raw, {"value": parsed}
    except Exception:
        return raw, {"raw_body": raw.decode("utf-8", errors="replace")[:8192]}


def _spawn_fire(automation, payload: dict[str, Any]) -> None:
    """Background-spawn the fire so the webhook ack is fast. We do
    this on `asyncio.create_task` rather than a queue because each
    webhook handler is already inside an event loop; one task per
    event is appropriate concurrency."""
    asyncio.create_task(
        auto_router.fire(automation, payload),
        name=f"automation_fire:{automation.id}",
    )


# --------------------------------------------------------------------------- #
# Direct: URL-as-secret
# --------------------------------------------------------------------------- #


@router.post("/webhooks/auto/{webhook_secret}")
async def direct_webhook(webhook_secret: str, request: Request) -> Response:
    """URL secret is the credential. We look up by secret (globally
    unique), reject if not found / paused. Industry pattern (Zapier,
    n8n, Make) -- see PR #93's replacement design discussion."""
    if not webhook_secret or len(webhook_secret) < 16:
        return Response(status_code=404)
    raw, payload = await _parse_body(request)
    async with get_session() as session:
        automation = await repo.resolve_by_webhook_secret(session, webhook_secret)
    if automation is None or automation.source != "direct":
        # 404 (not 401) so probing scripts can't tell a valid-but-paused
        # secret from a bogus one.
        return Response(status_code=404)
    if automation.is_paused:
        log.info("automation_webhook_paused_skipped", automation_id=str(automation.id))
        return Response(status_code=202, content=b'{"ok":true,"status":"paused"}')
    _spawn_fire(automation, payload)
    return Response(content=b'{"ok":true}', media_type="application/json")


# --------------------------------------------------------------------------- #
# Pipedream-routed: HMAC with per-trigger key
# --------------------------------------------------------------------------- #


@router.post("/webhooks/pipedream/{automation_id}")
async def pipedream_webhook(automation_id: str, request: Request) -> Response:
    try:
        aid = uuid.UUID(automation_id)
    except ValueError:
        return Response(status_code=404)
    raw, payload = await _parse_body(request)

    async with get_session() as session:
        automation = (
            await session.execute(
                # `select` is local to keep this file self-contained
                # rather than re-exporting another repo helper.
                _select_automation_by_id(aid)
            )
        ).scalar_one_or_none()
    if automation is None or automation.source != "pipedream":
        return Response(status_code=404)

    # Decrypt the stored per-trigger signing key, then verify the
    # request signature. If decryption fails we 500 (means crypto
    # config drifted -- not a user-visible problem to mask).
    if not automation.external_trigger_key_encrypted:
        log.error(
            "pipedream_automation_missing_signing_key",
            automation_id=str(aid),
        )
        return Response(status_code=500)
    try:
        signing_key = decrypt_token(automation.external_trigger_key_encrypted)
    except TokenCryptoError as exc:
        log.error("pipedream_signing_key_decrypt_failed", error=str(exc))
        return Response(status_code=500)

    signature = request.headers.get("x-pd-signature") or request.headers.get(
        "x-pipedream-signature"
    )
    if not _triggers.verify_pipedream_signature(
        raw_body=raw,
        signature_header=signature or "",
        signing_key=signing_key,
    ):
        return Response(status_code=401)

    if automation.is_paused:
        log.info("automation_webhook_paused_skipped", automation_id=str(automation.id))
        return Response(status_code=202, content=b'{"ok":true,"status":"paused"}')
    _spawn_fire(automation, payload)
    return Response(content=b'{"ok":true}', media_type="application/json")


# --------------------------------------------------------------------------- #
# Composio-routed: HMAC with account-wide secret
# --------------------------------------------------------------------------- #


@router.post("/webhooks/composio/{automation_id}")
async def composio_webhook(automation_id: str, request: Request) -> Response:
    try:
        aid = uuid.UUID(automation_id)
    except ValueError:
        return Response(status_code=404)
    raw, payload = await _parse_body(request)

    async with get_session() as session:
        automation = (
            await session.execute(_select_automation_by_id(aid))
        ).scalar_one_or_none()
    if automation is None or automation.source != "composio":
        return Response(status_code=404)

    webhook_id = request.headers.get("webhook-id", "")
    webhook_ts = request.headers.get("webhook-timestamp", "")
    signature = request.headers.get("webhook-signature", "")
    if not _triggers.verify_composio_signature(
        raw_body=raw,
        webhook_id=webhook_id,
        webhook_timestamp=webhook_ts,
        signature_header=signature,
    ):
        return Response(status_code=401)

    if automation.is_paused:
        log.info("automation_webhook_paused_skipped", automation_id=str(automation.id))
        return Response(status_code=202, content=b'{"ok":true,"status":"paused"}')
    _spawn_fire(automation, payload)
    return Response(content=b'{"ok":true}', media_type="application/json")


# --------------------------------------------------------------------------- #
# Local helper: select-by-id. Inline to avoid leaking workspace-aware
# lookups to anonymous webhook callers; these endpoints address rows
# directly by UUID, not by (workspace, name).
# --------------------------------------------------------------------------- #


def _select_automation_by_id(aid: uuid.UUID):
    from sqlalchemy import select

    from app.db.models import Automation

    return select(Automation).where(Automation.id == aid)


__all__ = ["router"]
