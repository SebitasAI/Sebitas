"""Thin async HTTP client for the Pipedream Connect REST API (via aiohttp).

Auth is OAuth client-credentials -> short-lived access token (cached). All
Connect calls are scoped to the project + environment. We never request or store
provider credentials; Pipedream runs actions with the connected account and
returns only results.
"""

from __future__ import annotations

import time

import aiohttp
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)

_BASE = "https://api.pipedream.com/v1"
_token: dict = {"value": None, "exp": 0.0}


class PipedreamHTTPError(Exception):
    """Raised when Pipedream Connect returns a non-2xx status. Carries the status
    code and the raw body so the provider layer can map it to a structured
    IntegrationError (and the gateway in turn to an actionable user message).

    This module stays a low-level HTTP client; it doesn't try to classify the
    error. Classification lives in PipedreamProvider."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body or ""
        super().__init__(f"Pipedream HTTP {status}: {self.body[:200]}")


async def _access_token() -> str:
    now = time.time()
    if _token["value"] and _token["exp"] - 60 > now:
        return _token["value"]
    s = get_settings()
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{_BASE}/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": s.pipedream_client_id,
                "client_secret": s.pipedream_client_secret,
            },
        ) as resp:
            data = await resp.json()
            resp.raise_for_status()
    _token["value"] = data["access_token"]
    _token["exp"] = now + int(data.get("expires_in", 3600))
    return _token["value"]


def _project_base() -> str:
    return f"{_BASE}/connect/{get_settings().pipedream_project_id}"


async def _headers() -> dict:
    return {
        "Authorization": f"Bearer {await _access_token()}",
        "X-PD-Environment": get_settings().pipedream_environment,
        "Content-Type": "application/json",
    }


async def _check(resp, data) -> None:
    if resp.status >= 400:
        body = data if isinstance(data, str) else str(data)
        raise PipedreamHTTPError(resp.status, body)


async def list_accounts(external_user_id: str) -> list[dict]:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{_project_base()}/accounts",
            params={"external_user_id": external_user_id, "include_credentials": "false"},
            headers=await _headers(),
        ) as resp:
            data = await resp.json()
            await _check(resp, data)
    return data.get("data", []) if isinstance(data, dict) else (data or [])


async def search_actions(app: str, query: str | None = None) -> list[dict]:
    params = {"app": app}
    if query:
        params["q"] = query
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{_project_base()}/actions", params=params, headers=await _headers()
        ) as resp:
            data = await resp.json()
            await _check(resp, data)
    return data.get("data", []) if isinstance(data, dict) else (data or [])


async def run_action(external_user_id: str, component_id: str, configured_props: dict) -> dict:
    s = get_settings()
    body = {
        "external_user_id": external_user_id,
        "id": component_id,
        "configured_props": configured_props,
    }
    timeout = aiohttp.ClientTimeout(total=s.integration_action_timeout)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{_project_base()}/actions/run", json=body, headers=await _headers()
        ) as resp:
            data = await resp.json()
            await _check(resp, data)
    return data


async def delete_account(account_id: str) -> bool:
    """Delete a connected account at Pipedream. Returns False if it didn't
    exist (idempotent), True if it was deleted, raises on other errors."""
    async with aiohttp.ClientSession() as session:
        async with session.delete(
            f"{_project_base()}/accounts/{account_id}", headers=await _headers()
        ) as resp:
            if resp.status == 404:
                return False
            if resp.status >= 400:
                body = await resp.text()
                raise PipedreamHTTPError(resp.status, body)
            return True


async def get_component(component_id: str) -> dict:
    """Component (action / source) definition, including its configurable_props.
    Used to discover the AUTH prop name for an action (Pipedream's convention is
    `name = "app"` with `type = "app"` and `app = <slug>`, NOT the slug itself
    as a top-level key)."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{_project_base()}/components/{component_id}", headers=await _headers()
        ) as resp:
            data = await resp.json()
            await _check(resp, data)
    return data.get("data") if isinstance(data, dict) and "data" in data else data


async def create_connect_token(external_user_id: str, webhook_uri: str | None = None) -> dict:
    body: dict = {"external_user_id": external_user_id}
    if webhook_uri:
        body["webhook_uri"] = webhook_uri
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{_project_base()}/tokens", json=body, headers=await _headers()
        ) as resp:
            data = await resp.json()
            await _check(resp, data)
    return data
