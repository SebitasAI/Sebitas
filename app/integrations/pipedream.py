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
    """List Pipedream's actions for a given app.

    Resolves our user-friendly slug to Pipedream's `name_slug` first.
    Without this, queries for apps whose slug diverges from Pipedream's
    (salesforce -> salesforce_rest_api, slack -> slack_bot, etc.) return
    0 actions and downstream code mistakenly believes the app has no
    catalog -- which is the same root cause as the connect-link bug
    fixed in `_mint_connect_link` (PR #122). Single boundary fix here
    means catalog_skills, gateway.find_actions, and the daily sweeper
    are all auto-corrected without per-caller plumbing.
    """
    resolved = await resolve_app_slug(app)
    params = {"app": resolved}
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


# --------------------------------------------------------------------------- #
# App-slug resolution (user-friendly slug -> Pipedream's `name_slug`)
# --------------------------------------------------------------------------- #


# In-process cache of resolved slugs. Pipedream's `name_slug` rarely
# changes once an app exists in their catalog, so a 6h TTL is safe and
# saves a /v1/apps query per connect attempt.
_slug_cache: dict[str, tuple[str, float]] = {}
_SLUG_TTL_S: float = 6 * 60 * 60


async def resolve_app_slug(user_slug: str) -> str:
    """Resolve a user-friendly app slug (the one our agent / catalog
    uses, e.g. `salesforce`) to the `name_slug` Pipedream's Connect
    UI expects (e.g. `salesforce_rest_api`).

    Why this exists: Pipedream's connector slugs don't always match
    the user-friendly app name. `gong` matches `gong`, but
    `salesforce` is actually `salesforce_rest_api` in their catalog,
    `notion` may be `notion_api`, etc. Passing the wrong slug as
    `&app=...` on the connect link makes Pipedream show "App not
    found". This function bridges the gap GENERICALLY: search the
    catalog by query and pick the closest match.

    Strategy (in order):
      1. Exact match: if `/v1/apps?q=<user_slug>` returns an app
         whose `name_slug` equals `user_slug`, use it.
      2. Substring/prefix match: pick the first result whose
         `name_slug` starts with the user_slug + `_`, OR whose
         `name_slug` contains the user_slug as a token. This covers
         `salesforce` -> `salesforce_rest_api`, `quickbooks` ->
         `quickbooks_online`, etc.
      3. Top-1 fallback: if no fuzzy match, use the first result's
         `name_slug`. Pipedream's `q` already ranks by relevance.
      4. Last resort: return the original `user_slug`. Lets the
         connect link fail downstream with a clear "App not found"
         instead of silently routing to the wrong app.

    Cached for 6h in-process. Safe for concurrent callers; first one
    populates the cache, the rest read it.
    """
    if not user_slug:
        return user_slug
    key = user_slug.lower().strip()
    now = time.time()
    cached = _slug_cache.get(key)
    if cached and (now - cached[1]) < _SLUG_TTL_S:
        return cached[0]

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_BASE}/apps",
                headers=await _headers(),
                params={"q": key, "limit": "10"},
            ) as resp:
                if resp.status != 200:
                    log.info(
                        "pipedream_resolve_slug_query_failed",
                        slug=key, status=resp.status,
                    )
                    return user_slug
                data = await resp.json()
    except Exception as exc:  # noqa: BLE001
        log.info("pipedream_resolve_slug_errored", slug=key, error=str(exc)[:200])
        return user_slug

    items = data.get("data") or []
    if not isinstance(items, list) or not items:
        log.info("pipedream_resolve_slug_no_results", slug=key)
        return user_slug

    # Pass 1: exact name_slug match.
    for app in items:
        if not isinstance(app, dict):
            continue
        ns = (app.get("name_slug") or "").lower()
        if ns == key:
            _slug_cache[key] = (ns, now)
            return ns

    # Pass 2: substring / token match. Prefer slugs that START with
    # `<user_slug>_` (i.e. user_slug is the leading token of a
    # compound slug like `salesforce_rest_api`). Then any slug
    # containing the user_slug as a token.
    for app in items:
        if not isinstance(app, dict):
            continue
        ns = (app.get("name_slug") or "").lower()
        if ns.startswith(key + "_"):
            log.info("pipedream_resolve_slug_prefix_match", input=key, resolved=ns)
            _slug_cache[key] = (ns, now)
            return ns

    for app in items:
        if not isinstance(app, dict):
            continue
        ns = (app.get("name_slug") or "").lower()
        tokens = ns.split("_")
        if key in tokens:
            log.info("pipedream_resolve_slug_token_match", input=key, resolved=ns)
            _slug_cache[key] = (ns, now)
            return ns

    # Pass 3: top-1 by relevance. Pipedream's q-search already orders
    # results by name match; trust the first one if it has a slug.
    top = items[0]
    if isinstance(top, dict):
        ns = (top.get("name_slug") or "").lower()
        if ns:
            log.info("pipedream_resolve_slug_top1_match", input=key, resolved=ns)
            _slug_cache[key] = (ns, now)
            return ns

    # Last resort: pass through. The connect link will fail with "App
    # not found" but at least the failure mode is explicit.
    log.info("pipedream_resolve_slug_no_match", slug=key)
    return user_slug
