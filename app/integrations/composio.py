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


async def list_auth_configs(toolkit_slug: str | None = None) -> list[dict]:
    """List auth configs created in our Composio project (via dashboard or
    API). Each toolkit needs at least one auth config to be linkable; we
    pick the first match for `toolkit_slug` if a filter is given.

    Composio's response shape varies by API version — normalise into a flat
    list of dicts so the caller can iterate without branching."""
    params: dict = {}
    if toolkit_slug:
        params["toolkit_slug"] = toolkit_slug.lower()
    data = await _request("GET", "/auth_configs", params=params)
    if isinstance(data, dict):
        return list(data.get("items") or data.get("auth_configs") or data.get("data") or [])
    return list(data) if isinstance(data, list) else []


async def initiate_connection(
    *,
    user_id: str,
    toolkit_slug: str,
    callback_url: str | None = None,
) -> dict:
    """Start a connect flow for a user against a toolkit. Composio's v3
    endpoint is `POST /connected_accounts/link` (the older `/initiate` path
    is being retired and returns 404 on new accounts). Requires an
    `auth_config_id` — we resolve it from the project's auth configs
    filtered by toolkit_slug. If no auth config exists for the toolkit, the
    project owner has to create one in the Composio dashboard first.

    Returns the link payload, expected to contain a redirect URL the user
    clicks to authorize."""
    configs = await list_auth_configs(toolkit_slug)
    auth_config_id = None
    for c in configs:
        # Multiple possible id field names across Composio API versions.
        auth_config_id = c.get("id") or c.get("nano_id") or c.get("auth_config_id")
        if auth_config_id:
            break
    if not auth_config_id:
        raise ComposioHTTPError(
            status=412,  # treat as user-actionable: precondition failed.
            body=(
                f"No auth_config found for toolkit '{toolkit_slug}'. "
                "Create one at https://app.composio.dev (Integrations) for "
                "this toolkit + your project, then retry."
            ),
        )

    body: dict = {
        "user_id": user_id,
        # Composio expects the auth_config as a nested object in the body
        # of POST /connected_accounts/link. Older variants accept a top-level
        # `auth_config_id`; we pass both shapes for compatibility.
        "auth_config": {"id": auth_config_id},
        "auth_config_id": auth_config_id,
    }
    if callback_url:
        body["callback_url"] = callback_url
    return await _request("POST", "/connected_accounts/link", json=body)


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
