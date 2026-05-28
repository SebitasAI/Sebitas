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


async def list_accounts(external_user_id: str) -> list[dict]:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{_project_base()}/accounts",
            params={"external_user_id": external_user_id, "include_credentials": "false"},
            headers=await _headers(),
        ) as resp:
            data = await resp.json()
            resp.raise_for_status()
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
            resp.raise_for_status()
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
            if resp.status >= 400:
                raise RuntimeError(f"Pipedream {resp.status}: {data}")
    return data


async def create_connect_token(external_user_id: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{_project_base()}/tokens",
            json={"external_user_id": external_user_id},
            headers=await _headers(),
        ) as resp:
            data = await resp.json()
            resp.raise_for_status()
    return data
