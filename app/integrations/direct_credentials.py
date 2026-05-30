"""Per-tenant credential storage for HTTP-direct fallbacks.

When a provider's wrapper (Composio, Pipedream) is broken for a specific
action — e.g. Composio strips `database_id` from POST /api/card and Metabase
rejects — we bypass the provider and call the app's REST API ourselves with
credentials stored in `IntegrationConnection.direct_credentials_encrypted`
(Fernet-encrypted JSON, key in Doppler).

Replaces the env-var fallback added in PR #54 (METABASE_FALLBACK_*). The
env-var path served as a workaround for a single tenant; scaling to N
tenants required moving credentials into a tenant-scoped column.

Shape of the decrypted JSON (per-app, free-form): for Metabase today,
`{"api_key": "mb_...", "base_url": "https://<tenant>.metabaseapp.com"}`.
Other apps will use their own shape; readers should accept what they need
and not validate the rest.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from sqlalchemy import select

from app.db.models import IntegrationConnection
from app.db.session import get_session
from app.slack.crypto import TokenCryptoError, decrypt_token, encrypt_token

log = structlog.get_logger(__name__)


async def get_direct_credentials(
    workspace_id: uuid.UUID, app: str
) -> dict[str, Any] | None:
    """Return the decrypted credentials dict for (workspace, app), or None
    if the row is missing or the column is empty. Logs (but does not raise)
    on decrypt errors so a single bad row doesn't break the action call;
    the caller treats None as 'no fallback available'."""
    async with get_session() as session:
        row = (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.app == app,
                )
            )
        ).scalar_one_or_none()
    if row is None or not row.direct_credentials_encrypted:
        return None
    try:
        plain = decrypt_token(row.direct_credentials_encrypted)
    except TokenCryptoError as exc:
        log.warning(
            "direct_credentials_decrypt_failed",
            workspace_id=str(workspace_id), app=app, error=str(exc)[:200],
        )
        return None
    try:
        creds = json.loads(plain)
    except json.JSONDecodeError:
        log.warning(
            "direct_credentials_invalid_json",
            workspace_id=str(workspace_id), app=app,
        )
        return None
    if not isinstance(creds, dict):
        return None
    return creds


async def set_direct_credentials(
    workspace_id: uuid.UUID, app: str, credentials: dict[str, Any],
) -> None:
    """Encrypt and persist `credentials` on the IntegrationConnection row
    for (workspace, app). Creates the row if it doesn't exist (status set to
    'direct' to indicate the connection is served entirely by HTTP-direct
    rather than via Pipedream/Composio).

    Use cases:
    - Admin onboarding a new tenant whose key we have: backfill via script.
    - Future: a user pasting their key in a DM with the bot (we capture it
      from the message, encrypt, store; never logged).
    """
    payload = json.dumps(credentials, separators=(",", ":"))
    cipher = encrypt_token(payload)
    async with get_session() as session:
        row = (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.app == app,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = IntegrationConnection(
                workspace_id=workspace_id,
                app=app,
                # No upstream provider for direct-only connections; we
                # still need to satisfy the provider check constraint, so
                # mark as 'composio' (the bypass path lives there). When we
                # add a 'direct' provider properly the CHECK constraint
                # needs to be extended.
                provider="composio",
                status="connected",
                direct_credentials_encrypted=cipher,
            )
            session.add(row)
        else:
            row.direct_credentials_encrypted = cipher
            if row.status not in ("connected", "pending"):
                row.status = "connected"
        await session.commit()
    log.info(
        "direct_credentials_stored",
        workspace_id=str(workspace_id), app=app, keys=sorted(credentials.keys()),
    )


async def clear_direct_credentials(workspace_id: uuid.UUID, app: str) -> bool:
    """Wipe the credentials column on the matching row. Returns True if a
    row was touched. Used when rotating keys or disconnecting."""
    async with get_session() as session:
        row = (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.app == app,
                )
            )
        ).scalar_one_or_none()
        if row is None or row.direct_credentials_encrypted is None:
            return False
        row.direct_credentials_encrypted = None
        await session.commit()
    log.info("direct_credentials_cleared", workspace_id=str(workspace_id), app=app)
    return True
