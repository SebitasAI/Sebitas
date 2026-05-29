"""Thin HTTP client for Composio's REST API.

Sibling of `app/integrations/pipedream.py`: same role, different provider.
Composio's API is documented at https://docs.composio.dev/api-reference.
Auth: a project API key in the `x-api-key` header. No OAuth dance from our
side — Composio handles per-end-user OAuth via its hosted auth UI; we just
mint a connect link and receive the connection id on completion.

This client deliberately does NOT use the official Composio SDK to avoid an
extra dependency and to keep the same async-aiohttp style as the rest of
the integrations module. Wraps every non-2xx into `ComposioHTTPError` for
the provider layer to translate into structured `IntegrationError` kinds.

Endpoints used (v3 surface):
  GET    /toolkits/{slug}                       — does this toolkit exist?
  GET    /tools?toolkit_slug={slug}              — list tools for a toolkit
  GET    /tools/{slug}                          — tool schema (props)
  POST   /tools/execute/{slug}                  — invoke a tool
  POST   /connected_accounts/initiate           — start the connect flow
  GET    /connected_accounts                    — list connections
  DELETE /connected_accounts/{id}               — disconnect
"""

from __future__ import annotations

from typing import Any

import aiohttp
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)


class ComposioHTTPError(Exception):
    """Raised on any non-2xx response from Composio. The provider layer maps
    this to the structured `IntegrationError` kind."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"composio HTTP {status}: {body[:200]}")


def _settings_or_raise():
    s = get_settings()
    if not s.composio_api_key:
        raise ComposioHTTPError(
            status=0,
            body="COMPOSIO_API_KEY is not set; ComposioProvider unavailable.",
        )
    return s


def _headers(s) -> dict:
    return {
        "x-api-key": s.composio_api_key,
        "content-type": "application/json",
        "accept": "application/json",
    }


async def _request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json: dict | None = None,
) -> Any:
    """Single HTTP round-trip. Returns parsed JSON on 2xx, raises
    ComposioHTTPError otherwise."""
    s = _settings_or_raise()
    url = f"{s.composio_base_url.rstrip('/')}{path}"
    timeout = aiohttp.ClientTimeout(total=s.integration_action_timeout)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(
            method, url, headers=_headers(s), params=params, json=json,
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                log.warning(
                    "composio_http_error",
                    method=method, path=path, status=resp.status, body=text[:300],
                )
                raise ComposioHTTPError(status=resp.status, body=text)
            if not text:
                return {}
            try:
                return await resp.json(content_type=None)
            except Exception:  # noqa: BLE001
                return {"raw": text}


# ---- Public surface (called by ComposioProvider) -------------------------- #


async def toolkit_exists(slug: str) -> bool:
    """Check if Composio has a toolkit for this app slug. Cheap (single GET)
    used by the gateway at connect-time to decide routing."""
    try:
        await _request("GET", f"/toolkits/{slug.lower()}")
        return True
    except ComposioHTTPError as e:
        if e.status == 404:
            return False
        raise


async def list_tools(toolkit_slug: str, query: str | None = None) -> list[dict]:
    """Tools available in this toolkit. Used to populate `find_actions`.
    The `query` filter is a substring match handled server-side."""
    params: dict = {"toolkit_slug": toolkit_slug.lower()}
    if query:
        params["search"] = query
    data = await _request("GET", "/tools", params=params)
    if isinstance(data, dict):
        return list(data.get("items") or data.get("tools") or [])
    return list(data) if isinstance(data, list) else []


async def get_tool(tool_slug: str) -> dict:
    """Full schema for a tool: name, description, input_parameters."""
    return await _request("GET", f"/tools/{tool_slug}")


async def execute_tool(
    *,
    tool_slug: str,
    user_id: str,
    arguments: dict,
    connected_account_id: str | None = None,
) -> dict:
    """Invoke a tool with stored credentials. `user_id` identifies the end
    user (we pass workspace_id as user_id, same pattern as Pipedream's
    external_user_id). When more than one connected_account exists for the
    user + toolkit, `connected_account_id` disambiguates."""
    body: dict = {"user_id": user_id, "arguments": arguments}
    if connected_account_id:
        body["connected_account_id"] = connected_account_id
    return await _request("POST", f"/tools/execute/{tool_slug}", json=body)


async def initiate_connection(
    *,
    user_id: str,
    toolkit_slug: str,
    callback_url: str | None = None,
) -> dict:
    """Start a new OAuth/API-key connect flow for a user against a toolkit.
    Returns `{redirect_url, connection_id, ...}`. The user clicks the
    redirect_url, completes auth on Composio's hosted UI, and Composio fires
    a webhook (or we poll connection_id) to know it succeeded."""
    body: dict = {"user_id": user_id, "toolkit_slug": toolkit_slug.lower()}
    if callback_url:
        body["callback_url"] = callback_url
    return await _request("POST", "/connected_accounts/initiate", json=body)


async def list_connections(*, user_id: str, toolkit_slug: str | None = None) -> list[dict]:
    """Connections this user has authorized, optionally filtered to one
    toolkit. Used by the connect-complete polling fallback and the
    `list_integrations` tool."""
    params: dict = {"user_id": user_id}
    if toolkit_slug:
        params["toolkit_slug"] = toolkit_slug.lower()
    data = await _request("GET", "/connected_accounts", params=params)
    if isinstance(data, dict):
        return list(data.get("items") or data.get("connections") or [])
    return list(data) if isinstance(data, list) else []


async def get_connection(connection_id: str) -> dict | None:
    """One connection by id. Returns None on 404 so callers can treat
    missing as "already disconnected / never existed"."""
    try:
        return await _request("GET", f"/connected_accounts/{connection_id}")
    except ComposioHTTPError as e:
        if e.status == 404:
            return None
        raise


async def delete_connection(connection_id: str) -> bool:
    """Disconnect a stored credential. Returns True if it existed and was
    deleted; False if it was already gone (404 is treated as idempotent
    success here, matching Pipedream's contract)."""
    try:
        await _request("DELETE", f"/connected_accounts/{connection_id}")
        return True
    except ComposioHTTPError as e:
        if e.status == 404:
            return False
        raise
