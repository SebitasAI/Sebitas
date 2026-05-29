"""Provider routing: pick which IntegrationProvider handles a given app
for a given workspace.

Two paths:

1. Existing connection (`IntegrationConnection` row exists for the
   workspace + app): the row's `provider` field is the source of truth.
   We never re-decide in the middle of a connection's life — that would
   silently move a tenant from one provider's credentials to another.

2. New connection (no row yet): the gateway prefers Composio when their
   catalogue has the toolkit, falls back to Pipedream otherwise. The
   chosen provider is then persisted on the new row during the connect
   flow so future invocations reuse the same decision.

The agent layer above this never sees the routing. It calls
`run_action("metabase", ...)`; the gateway resolves provider, the provider
calls its own API, the result flows back as-is. Adding a third provider
(MCP-direct, custom HTTP) means a new entry here + a new concrete
provider, nothing else.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select

from app.db.models import IntegrationConnection
from app.db.session import get_session
from app.integrations.composio_provider import get_composio_provider
from app.integrations.pipedream_provider import get_provider as get_pipedream_provider
from app.integrations.provider import IntegrationProvider

log = structlog.get_logger(__name__)


_PIPEDREAM = "pipedream"
_COMPOSIO = "composio"

# Order of preference when more than one provider can serve the same app.
# First match wins. To force a specific provider for a specific app
# regardless of catalogue availability, an explicit override dict could be
# added here later; we keep this simple while only two providers exist.
_PREFERENCE_ORDER = [_COMPOSIO, _PIPEDREAM]


def _provider_by_name(name: str) -> IntegrationProvider:
    if name == _COMPOSIO:
        return get_composio_provider()
    return get_pipedream_provider()


async def provider_for_existing_connection(
    workspace_id: uuid.UUID, app: str
) -> tuple[IntegrationProvider, str] | None:
    """Return (provider, provider_name) for a connection that already exists,
    or None if there's no row for this (workspace, app). Used by every
    action-time gateway call — read once, route once."""
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
        return None
    name = row.provider or _PIPEDREAM
    return _provider_by_name(name), name


async def decide_provider_for_new_connection(app: str) -> str:
    """No row yet for this (workspace, app): pick which provider to use
    for the upcoming connect flow. Probes the preference order; first
    provider whose catalogue has the toolkit wins. Falls back to Pipedream
    if all checks fail (Composio API down, key missing, etc.) so the user
    never gets stuck just because Composio is unreachable.
    """
    for name in _PREFERENCE_ORDER:
        if name == _COMPOSIO:
            provider = get_composio_provider()
            try:
                if await provider.has_toolkit(app):
                    return _COMPOSIO
            except Exception as exc:  # noqa: BLE001
                # Don't fail the connect attempt because the catalogue probe
                # failed; just skip this provider and try the next.
                log.warning(
                    "provider_catalog_probe_failed",
                    provider=name, app=app, error=str(exc)[:200],
                )
                continue
        elif name == _PIPEDREAM:
            # Pipedream has 2700+ apps; we assume it covers anything we ask
            # about unless we get a 404 at connect-time (handled there).
            return _PIPEDREAM
    return _PIPEDREAM


async def provider_for_app(
    workspace_id: uuid.UUID, app: str
) -> tuple[IntegrationProvider, str]:
    """Public entry point: get the right provider for any (workspace, app).
    Reads the existing row if any, else decides afresh.

    Returns a tuple (provider, provider_name). The name is also returned so
    callers can persist it on a freshly-created IntegrationConnection row.
    """
    existing = await provider_for_existing_connection(workspace_id, app)
    if existing is not None:
        return existing
    name = await decide_provider_for_new_connection(app)
    return _provider_by_name(name), name
