"""Unit tests for the integration provider routing.

The routing decides which IntegrationProvider serves a given (workspace, app):
- Existing connections honour the row's `provider` field.
- New connections probe Composio's catalogue first; fall back to Pipedream.

These tests cover both paths with mocked provider catalogues; integration
tests that exercise the real Composio API live elsewhere (require an API
key in the env)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_existing_pipedream_connection_routes_to_pipedream(
    fake_r2, db_session, workspace
):
    """A row with provider='pipedream' goes to the Pipedream provider, no
    catalogue probe needed."""
    from app.db.models import IntegrationConnection
    from app.integrations.routing import provider_for_app

    db_session.add(IntegrationConnection(
        workspace_id=workspace.id,
        app="notion",
        provider="pipedream",
        pipedream_account_id="apn_legacy_123",
        status="connected",
    ))
    await db_session.commit()

    provider, name = await provider_for_app(workspace.id, "notion")
    assert name == "pipedream"
    assert provider.name == "pipedream"


@pytest.mark.asyncio
async def test_existing_composio_connection_routes_to_composio(
    fake_r2, db_session, workspace
):
    """A row with provider='composio' goes to the Composio provider, no
    catalogue probe needed (even though Composio is "preferred", the existing
    decision wins)."""
    from app.db.models import IntegrationConnection
    from app.integrations.routing import provider_for_app

    db_session.add(IntegrationConnection(
        workspace_id=workspace.id,
        app="metabase",
        provider="composio",
        pipedream_account_id="cz_conn_999",
        status="connected",
    ))
    await db_session.commit()

    provider, name = await provider_for_app(workspace.id, "metabase")
    assert name == "composio"
    assert provider.name == "composio"


@pytest.mark.asyncio
async def test_new_connection_prefers_composio_when_app_in_catalog(
    fake_r2, db_session, workspace
):
    """No existing row, Composio reports the toolkit exists -> Composio."""
    from app.integrations.routing import provider_for_app

    with patch(
        "app.integrations.composio_provider.cz.toolkit_exists",
        new=AsyncMock(return_value=True),
    ):
        provider, name = await provider_for_app(workspace.id, "metabase")
    assert name == "composio"


@pytest.mark.asyncio
async def test_new_connection_falls_back_to_pipedream_when_composio_misses(
    fake_r2, db_session, workspace
):
    """No existing row, Composio doesn't have the toolkit -> Pipedream."""
    from app.integrations.routing import provider_for_app

    with patch(
        "app.integrations.composio_provider.cz.toolkit_exists",
        new=AsyncMock(return_value=False),
    ):
        provider, name = await provider_for_app(workspace.id, "obscure_saas")
    assert name == "pipedream"


@pytest.mark.asyncio
async def test_new_connection_falls_back_to_pipedream_when_composio_unreachable(
    fake_r2, db_session, workspace
):
    """If the Composio catalogue probe raises (network, bad key, etc.), the
    routing still produces a working answer instead of crashing the user."""
    from app.integrations.composio import ComposioHTTPError
    from app.integrations.routing import provider_for_app

    with patch(
        "app.integrations.composio_provider.cz.toolkit_exists",
        new=AsyncMock(side_effect=ComposioHTTPError(500, "internal")),
    ):
        provider, name = await provider_for_app(workspace.id, "metabase")
    assert name == "pipedream"
