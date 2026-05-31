"""Aggregate catalog of available integrations (slice T-6).

Lists every app the workspace can connect: Composio's toolkits + Pipedream's
apps, deduped by slug. When both providers offer the same app, Composio
wins (per Sam's call -- Composio's tool coverage is wider for our target
apps).

The catalog is cached in-process for `_CATALOG_TTL_S` (1h) because the
provider catalogs change slowly and the page-load wants <100ms response
times against ~3000 entries. First request triggers a fetch; subsequent
requests within the TTL serve from memory.

`POPULAR_SLUGS` is the curated short-list shown on the "Popular
integrations" tab. Editable here -- redeploy to update. v2 would move
this to a DB row admins can edit, but for v1 a code change is fine.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import aiohttp
import structlog

from app.config import get_settings
from app.integrations import composio as composio_api

log = structlog.get_logger(__name__)


# Popular integrations (curated). Order matters -- this is the order
# they're shown on the "Popular" tab. Use the canonical slug (lowercase)
# that BOTH Composio and Pipedream use; the catalog merge will resolve
# which provider backs each.
POPULAR_SLUGS: list[str] = [
    "slack",
    "googledrive",
    "googlesheets",
    "gmail",
    "notion",
    "linear",
    "github",
    "figma",
    "stripe",
    "hubspot",
    "salesforce",
    "jira",
    "asana",
    "discord",
    "clickup",
    "zoom",
    "openai",
    "anthropic",
    "googlecalendar",
    "airtable",
]


@dataclass(frozen=True)
class CatalogApp:
    """One entry in the catalog. `provider` is which integration backs it
    today (preferring Composio when both offer it). `popular` is True iff
    the slug is in POPULAR_SLUGS."""

    slug: str
    name: str
    description: str | None
    logo_url: str | None
    provider: str  # "composio" | "pipedream"
    categories: list[str]
    popular: bool


_CATALOG_TTL_S = 60 * 60  # 1 hour
_catalog_cache: list[CatalogApp] | None = None
_catalog_cache_expires_at: float = 0.0
_catalog_lock = asyncio.Lock()


# --------------------------------------------------------------------------- #
# Provider fetchers
# --------------------------------------------------------------------------- #


async def _list_composio_toolkits() -> list[dict]:
    """Page through Composio's /toolkits endpoint. The API returns up to
    100 per page; we follow the cursor until exhausted. Defensive on the
    response shape (Composio has changed it before)."""
    s = get_settings()
    if not s.composio_api_key:
        log.info("catalog_composio_skipped_no_key")
        return []

    out: list[dict] = []
    cursor: str | None = None
    pages = 0
    while True:
        pages += 1
        if pages > 60:  # safety: 6000 toolkits is far beyond reality
            log.warning("catalog_composio_pagination_cap", pages=pages)
            break
        params: dict = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        try:
            data = await composio_api._request("GET", "/toolkits", params=params)
        except composio_api.ComposioHTTPError as exc:
            log.warning("catalog_composio_fetch_failed", error=str(exc))
            break
        items = []
        next_cursor = None
        if isinstance(data, dict):
            items = (
                data.get("items")
                or data.get("toolkits")
                or data.get("data")
                or []
            )
            # Composio paginates with either `next_cursor` at the top or
            # `nextCursor` nested; cover both.
            next_cursor = (
                data.get("next_cursor")
                or data.get("nextCursor")
                or (data.get("pagination") or {}).get("next_cursor")
            )
        elif isinstance(data, list):
            items = data
        if not items:
            break
        out.extend(items)
        if not next_cursor:
            break
        cursor = next_cursor
    return out


async def _list_pipedream_apps() -> list[dict]:
    """Pipedream's /v1/apps endpoint. We send a single OAuth-style request
    with the project API key; pagination via `after` cursor. This is best-
    effort; if the env isn't configured we just skip and return [].
    """
    s = get_settings()
    if not (s.pipedream_client_id and s.pipedream_client_secret):
        log.info("catalog_pipedream_skipped_no_creds")
        return []

    # Pipedream requires an access token; the existing pipedream.py file
    # mints one. To avoid coupling we hit the v1/apps endpoint directly
    # with the project's API base.
    base_url = "https://api.pipedream.com/v1"
    out: list[dict] = []
    after: str | None = None
    pages = 0
    timeout = aiohttp.ClientTimeout(total=20.0)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Get an access token first (client credentials grant).
        token_url = f"{base_url}/oauth/token"
        try:
            async with session.post(
                token_url,
                json={
                    "grant_type": "client_credentials",
                    "client_id": s.pipedream_client_id,
                    "client_secret": s.pipedream_client_secret,
                },
            ) as resp:
                if resp.status != 200:
                    log.warning(
                        "catalog_pipedream_token_failed",
                        status=resp.status,
                    )
                    return []
                token_body = await resp.json()
                access_token = token_body.get("access_token")
        except Exception as exc:  # noqa: BLE001
            log.warning("catalog_pipedream_token_error", error=str(exc))
            return []
        if not access_token:
            return []

        headers = {"Authorization": f"Bearer {access_token}"}
        while True:
            pages += 1
            if pages > 60:
                log.warning("catalog_pipedream_pagination_cap", pages=pages)
                break
            url = f"{base_url}/apps"
            params: dict[str, str] = {"limit": "100"}
            if after:
                params["after"] = after
            try:
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status != 200:
                        log.warning(
                            "catalog_pipedream_apps_failed",
                            status=resp.status,
                        )
                        break
                    body = await resp.json()
            except Exception as exc:  # noqa: BLE001
                log.warning("catalog_pipedream_apps_error", error=str(exc))
                break
            items = body.get("data") or []
            if not items:
                break
            out.extend(items)
            page_info = body.get("page_info") or {}
            after = page_info.get("end_cursor")
            if not page_info.get("has_more") or not after:
                break
    return out


# --------------------------------------------------------------------------- #
# Normalization + merge
# --------------------------------------------------------------------------- #


def _normalize_composio(item: dict) -> CatalogApp | None:
    slug = (item.get("slug") or item.get("key") or "").lower().strip()
    if not slug:
        return None
    return CatalogApp(
        slug=slug,
        name=item.get("name") or slug.title(),
        description=item.get("description") or item.get("meta", {}).get("description"),
        logo_url=(
            item.get("logo_url")
            or item.get("logo")
            or (item.get("meta") or {}).get("logo")
            or (item.get("meta") or {}).get("logo_url")
        ),
        provider="composio",
        categories=list(item.get("categories") or []),
        popular=slug in POPULAR_SLUGS,
    )


def _normalize_pipedream(item: dict) -> CatalogApp | None:
    # Pipedream returns slugs like "google_sheets"; normalize to the
    # cross-provider form ("googlesheets") so dedupe works.
    raw = (item.get("name_slug") or item.get("name") or "").lower().strip()
    slug = raw.replace("_", "").replace("-", "")
    if not slug:
        return None
    return CatalogApp(
        slug=slug,
        name=item.get("name") or raw,
        description=item.get("description"),
        logo_url=item.get("img_src") or item.get("logo"),
        provider="pipedream",
        categories=list(item.get("categories") or []),
        popular=slug in POPULAR_SLUGS,
    )


def _merge(composio: list[CatalogApp], pipedream: list[CatalogApp]) -> list[CatalogApp]:
    """Composio wins on slug collisions; Pipedream-only apps stay as-is.
    Result is sorted by (popular DESC, name ASC) so the popular tab + the
    full grid both order intuitively."""
    by_slug: dict[str, CatalogApp] = {a.slug: a for a in composio}
    for a in pipedream:
        if a.slug in by_slug:
            continue
        by_slug[a.slug] = a
    merged = list(by_slug.values())
    merged.sort(key=lambda a: (not a.popular, a.name.lower()))
    return merged


# --------------------------------------------------------------------------- #
# Public surface
# --------------------------------------------------------------------------- #


async def get_catalog(*, force_refresh: bool = False) -> list[CatalogApp]:
    """Return the merged catalog. Cached for 1h; concurrent callers during
    a cold cache coalesce on a single fetch via `_catalog_lock`."""
    global _catalog_cache, _catalog_cache_expires_at
    now = time.monotonic()
    if (
        not force_refresh
        and _catalog_cache is not None
        and now < _catalog_cache_expires_at
    ):
        return _catalog_cache

    async with _catalog_lock:
        # Re-check under the lock (someone else may have populated it while
        # we awaited).
        now = time.monotonic()
        if (
            not force_refresh
            and _catalog_cache is not None
            and now < _catalog_cache_expires_at
        ):
            return _catalog_cache

        composio_raw, pipedream_raw = await asyncio.gather(
            _list_composio_toolkits(),
            _list_pipedream_apps(),
            return_exceptions=True,
        )
        # `gather` with return_exceptions wraps failures; replace with empty
        # lists so a single provider outage doesn't blank the whole catalog.
        if isinstance(composio_raw, BaseException):
            log.warning("catalog_composio_failed", error=str(composio_raw))
            composio_raw = []
        if isinstance(pipedream_raw, BaseException):
            log.warning("catalog_pipedream_failed", error=str(pipedream_raw))
            pipedream_raw = []

        composio_apps = [
            a for a in (_normalize_composio(i) for i in composio_raw) if a is not None
        ]
        pipedream_apps = [
            a for a in (_normalize_pipedream(i) for i in pipedream_raw) if a is not None
        ]
        merged = _merge(composio_apps, pipedream_apps)
        _catalog_cache = merged
        _catalog_cache_expires_at = time.monotonic() + _CATALOG_TTL_S
        log.info(
            "catalog_built",
            composio_count=len(composio_apps),
            pipedream_count=len(pipedream_apps),
            merged_count=len(merged),
        )
        return merged


def reset_catalog_cache_for_test() -> None:
    """Test hook -- clears the in-process cache so the next get_catalog()
    re-fetches."""
    global _catalog_cache, _catalog_cache_expires_at
    _catalog_cache = None
    _catalog_cache_expires_at = 0.0


__all__ = ["CatalogApp", "POPULAR_SLUGS", "get_catalog", "reset_catalog_cache_for_test"]
