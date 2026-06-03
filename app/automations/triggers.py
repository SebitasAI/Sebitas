"""Provisioning + signature verification for source-specific automations.

`direct` source needs nothing here: we generate a URL secret in the
repository and that's the credential. This module handles the two
provider-routed sources:

  - Pipedream: deploy a trigger via Pipedream Connect Triggers API
    (`POST /v1/connect/{project_id}/triggers`). Pipedream returns a
    per-trigger signing key once at creation -- we encrypt and store
    it. Inbound requests carry `x-pd-signature: t={ts},v1={hex}` with
    HMAC-SHA256 over `{timestamp}.{body}`.

  - Composio: create a trigger instance via `POST /api/v3/triggers/
    instance`. Composio signs with an account-wide secret already in
    Doppler (`composio_webhook_secret`). Inbound carries `webhook-id`,
    `webhook-timestamp`, `webhook-signature: v1,{base64}` with
    HMAC-SHA256 over `{webhook_id}.{timestamp}.{body}`.

The exact upstream request bodies are based on docs available at
authoring time -- a smoke run against real Pipedream / Composio
accounts is required to confirm. Marked TODO where assumptions are
load-bearing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

import aiohttp
import structlog
from fastapi import Request

from app.config import get_settings
from app.integrations import pipedream as _pd_client

log = structlog.get_logger(__name__)


# Composio API base used by their triggers endpoints. The
# `app.integrations.composio` client targets the same base; we don't
# import its `_request` because triggers aren't part of its surface
# yet, and keeping that client focused on tools/auth is cleaner than
# adding a sibling endpoint set.
_COMPOSIO_API_BASE = "https://backend.composio.dev"


class TriggerProvisioningError(Exception):
    """Raised when the upstream provider rejected the trigger create
    or returned an unexpected shape. The agent tool surfaces this to
    the user verbatim so they know to retry or fix the config."""


# --------------------------------------------------------------------------- #
# Pipedream
# --------------------------------------------------------------------------- #


async def provision_pipedream_trigger(
    *,
    component_id: str,
    configured_props: dict[str, Any],
    webhook_url: str,
    external_user_id: str,
) -> tuple[str, str]:
    """Create a Pipedream Connect trigger pointing at `webhook_url`.
    Returns (trigger_id, signing_key_plaintext). The signing key is
    returned ONCE -- we encrypt it before persisting (see
    repository.create_automation).

    `component_id` identifies the Pipedream source component (e.g.
    `langfuse-score-created`). `configured_props` are component-
    specific settings (account ids, filters). `external_user_id` is
    the workspace id, matching how we identify users to Pipedream
    elsewhere.

    TODO(verify-upstream): the exact request body shape below is
    inferred from the Connect Triggers docs. Confirm against an
    actual successful trigger deploy on smoke."""
    settings = get_settings()
    headers = await _pd_client._headers()  # type: ignore[attr-defined]
    url = (
        f"{_pd_client._BASE}/connect/{settings.pipedream_project_id}/triggers"
    )
    body = {
        "external_user_id": external_user_id,
        "id": component_id,
        "configured_props": configured_props,
        "webhook_url": webhook_url,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body, headers=headers) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise TriggerProvisioningError(
                    f"Pipedream trigger create failed ({resp.status}): {data}"
                )

    trigger_id = (
        data.get("id")
        or data.get("data", {}).get("id")
        or data.get("trigger_id")
    )
    signing_key = (
        data.get("signing_key")
        or data.get("data", {}).get("signing_key")
        or data.get("webhook_secret")
    )
    if not trigger_id or not signing_key:
        raise TriggerProvisioningError(
            f"Pipedream returned unexpected shape on trigger create: keys="
            f"{list(data.keys())}"
        )
    log.info(
        "pipedream_trigger_provisioned",
        trigger_id=trigger_id,
        component_id=component_id,
    )
    return trigger_id, signing_key


async def delete_pipedream_trigger(trigger_id: str) -> bool:
    """Best-effort cleanup. Caller already deleted our row; if this
    fails the upstream trigger lingers + keeps POSTing to a URL that
    now 404s. Pipedream eventually retires zombie triggers, and we
    log so ops can clean up if needed."""
    settings = get_settings()
    headers = await _pd_client._headers()  # type: ignore[attr-defined]
    url = (
        f"{_pd_client._BASE}/connect/{settings.pipedream_project_id}"
        f"/triggers/{trigger_id}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=headers) as resp:
                ok = resp.status < 400 or resp.status == 404
                if not ok:
                    body = await resp.text()
                    log.warning(
                        "pipedream_trigger_delete_failed",
                        trigger_id=trigger_id,
                        status=resp.status,
                        body=body[:200],
                    )
                return ok
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "pipedream_trigger_delete_exception",
            trigger_id=trigger_id,
            error=str(exc)[:200],
        )
        return False


# Verification window: requests older than this fail signature check
# (replay protection). Pipedream docs don't pin a specific value;
# 5 minutes matches Composio's default and Stripe / Slack conventions.
_REPLAY_TOLERANCE_S = 300


def verify_pipedream_signature(
    *, raw_body: bytes, signature_header: str, signing_key: str
) -> bool:
    """Verify `x-pd-signature: t={ts},v1={hex}`. Signed payload is
    `{timestamp}.{body}`. Returns False on shape mismatch, expired
    timestamp, or HMAC mismatch.

    `signing_key` is the per-trigger plaintext (caller decrypted)."""
    if not signature_header or not signing_key:
        return False
    parts = dict(
        kv.split("=", 1) for kv in signature_header.split(",") if "=" in kv
    )
    ts = parts.get("t")
    sig = parts.get("v1")
    if not ts or not sig:
        return False
    try:
        ts_int = int(ts)
    except ValueError:
        return False
    if abs(time.time() - ts_int) > _REPLAY_TOLERANCE_S:
        return False
    signed = f"{ts}.".encode() + raw_body
    expected = hmac.new(signing_key.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


# --------------------------------------------------------------------------- #
# Composio
# --------------------------------------------------------------------------- #


async def provision_composio_trigger(
    *,
    trigger_slug: str,
    user_id: str,
    config: dict[str, Any],
    webhook_url: str,
    connected_account_id: str | None = None,
) -> str:
    """Create a Composio trigger instance pointing at `webhook_url`.

    Correct upstream contract (verified 2026-06-03 against prod):

      POST /api/v3/trigger_instances/{TRIGGER_SLUG}/upsert
      body: {connected_account_id, trigger_config}

    The previous path (`/api/v3/triggers/instance`) returns 404 with
    an HTML body, which our previous error handler then tried to
    decode as JSON and crashed with `ContentTypeError`. Two fixes
    here:

      1. Use the correct path.
      2. Read the response as text first, then parse JSON only when
         the Content-Type is `application/json`. Surface HTML / empty
         bodies as a structured `TriggerProvisioningError` instead of
         leaking a JSON decode error to the agent.

    `connected_account_id` is REQUIRED by Composio: a trigger
    instance is always scoped to an already-connected account, not
    to a free-floating user id. Callers must look up the user's
    Composio connection for the trigger's toolkit and pass its id.
    """
    settings = get_settings()
    api_key = getattr(settings, "composio_api_key", None)
    if not api_key:
        raise TriggerProvisioningError(
            "composio_api_key no configurada."
        )
    if not connected_account_id:
        raise TriggerProvisioningError(
            "Para crear este trigger necesito el id de la cuenta "
            f"conectada del usuario en Composio para `{trigger_slug}`. "
            "Confirma que el usuario tiene la integración conectada "
            "antes de crear la automation."
        )
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }
    body = {
        "connected_account_id": connected_account_id,
        "trigger_config": dict(config) if config else {},
    }
    if webhook_url:
        # Composio's docs list `webhook_url` as optional on upsert
        # (project-level webhook can be used instead). We pass it
        # anyway so per-trigger webhook overrides work.
        body["webhook_url"] = webhook_url

    url = (
        f"{_COMPOSIO_API_BASE}/api/v3/trigger_instances/"
        f"{trigger_slug}/upsert"
    )
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body, headers=headers) as resp:
            text = await resp.text()
            ct = (resp.headers.get("content-type") or "").lower()
            if "application/json" in ct:
                try:
                    data = __import__("json").loads(text) if text else {}
                except Exception:  # noqa: BLE001
                    data = {}
            else:
                data = {}
            if resp.status >= 400:
                # Prefer the structured `error.message` Composio sends
                # when content-type is JSON; fall back to the raw text
                # prefix when it's HTML / unknown so the dev still sees
                # something useful in the agent reply.
                err_msg = (
                    data.get("error", {}).get("message")
                    if isinstance(data, dict) else None
                ) or (text[:200] if text else "(empty body)")
                raise TriggerProvisioningError(
                    f"Composio trigger create failed ({resp.status}): {err_msg}"
                )
    trigger_id = (
        data.get("id")
        or data.get("trigger_id")
        or data.get("triggerInstanceId")
        or data.get("data", {}).get("id")
    )
    if not trigger_id:
        raise TriggerProvisioningError(
            f"Composio returned unexpected shape on trigger create: "
            f"keys={list(data.keys()) if isinstance(data, dict) else type(data).__name__}"
        )
    log.info(
        "composio_trigger_provisioned",
        trigger_id=trigger_id,
        trigger_slug=trigger_slug,
        connected_account_id=connected_account_id,
    )
    return trigger_id


async def delete_composio_trigger(trigger_id: str) -> bool:
    """Best-effort delete. See `delete_pipedream_trigger` rationale."""
    settings = get_settings()
    api_key = getattr(settings, "composio_api_key", None)
    if not api_key:
        return False
    headers = {"x-api-key": api_key}
    url = f"{_COMPOSIO_API_BASE}/api/v3/trigger_instances/manage/{trigger_id}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=headers) as resp:
                ok = resp.status < 400 or resp.status == 404
                if not ok:
                    body = await resp.text()
                    log.warning(
                        "composio_trigger_delete_failed",
                        trigger_id=trigger_id,
                        status=resp.status,
                        body=body[:200],
                    )
                return ok
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "composio_trigger_delete_exception",
            trigger_id=trigger_id,
            error=str(exc)[:200],
        )
        return False


def verify_composio_signature(
    *,
    raw_body: bytes,
    webhook_id: str,
    webhook_timestamp: str,
    signature_header: str,
) -> bool:
    """Verify Composio's webhook signature.

    Signed input: `{webhook_id}.{timestamp}.{body}`. Header format:
    `webhook-signature: v1,{base64_signature}`. Account-wide secret
    lives in Doppler. Returns False on any mismatch (replay-tolerant
    by default, 5min)."""
    settings = get_settings()
    secret = getattr(settings, "composio_webhook_secret", None)
    if not secret:
        log.warning("composio_webhook_secret_missing")
        return False
    if not (webhook_id and webhook_timestamp and signature_header):
        return False
    try:
        ts_int = int(webhook_timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts_int) > _REPLAY_TOLERANCE_S:
        return False
    # Strip the `v1,` prefix if present.
    sig = signature_header
    if sig.startswith("v1,"):
        sig = sig[3:]
    signed = f"{webhook_id}.{webhook_timestamp}.".encode() + raw_body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).digest()
    try:
        provided = base64.b64decode(sig)
    except Exception:  # noqa: BLE001
        return False
    return hmac.compare_digest(expected, provided)


# --------------------------------------------------------------------------- #
# URL helpers
# --------------------------------------------------------------------------- #


def webhook_base_url() -> str:
    """Public base URL where Misterr serves webhooks. Used to compute
    the `webhook_url` passed to Pipedream / Composio at trigger creation
    AND surfaced to the user for the `direct` source. Lives in the
    same setting as the integrations webhook base."""
    settings = get_settings()
    return (getattr(settings, "public_base_url", "") or "").rstrip("/")


def direct_webhook_url(webhook_secret: str) -> str:
    return f"{webhook_base_url()}/webhooks/auto/{webhook_secret}"


def pipedream_webhook_url(automation_id: str) -> str:
    return f"{webhook_base_url()}/webhooks/pipedream/{automation_id}"


def composio_webhook_url(automation_id: str) -> str:
    return f"{webhook_base_url()}/webhooks/composio/{automation_id}"


__all__ = [
    "TriggerProvisioningError",
    "provision_pipedream_trigger",
    "delete_pipedream_trigger",
    "verify_pipedream_signature",
    "provision_composio_trigger",
    "delete_composio_trigger",
    "verify_composio_signature",
    "webhook_base_url",
    "direct_webhook_url",
    "pipedream_webhook_url",
    "composio_webhook_url",
]
