"""Thin Clerk REST client for Spaces.

Used by ConvexSharedSpaceBackend at deploy time to resolve `{email}` entries
in `access_list` to canonical Clerk `user_id`s. If the email isn't registered
in Clerk yet, we keep the entry pending (user_id empty + email stored) and
the Convex `assertSpaceAccess` resolves it lazily on first login.

Only `secret_key` lives in our process; the publishable key is for the
frontend bundle. Neither leaves the trust boundary they belong to.
"""

from __future__ import annotations

import aiohttp
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)

_BASE = "https://api.clerk.com/v1"


async def resolve_email_to_user_id(email: str) -> str | None:
    """Returns the Clerk `user_id` for `email`, or None if the address isn't
    registered yet. Network errors / missing secret -> None (caller treats
    as 'pending' and stores the email)."""
    if not email:
        return None
    settings = get_settings()
    if not settings.clerk_secret_key:
        log.warning("clerk_secret_key_missing")
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{_BASE}/users",
                params={"email_address": email, "limit": 1},
                headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 401:
                    log.warning("clerk_auth_failed")
                    return None
                data = await resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("clerk_lookup_failed", email=email, error=str(exc))
        return None

    # Clerk returns either {data: [...]} or [...] depending on endpoint version.
    rows = data.get("data") if isinstance(data, dict) and "data" in data else data
    if isinstance(rows, list) and rows:
        first = rows[0]
        if isinstance(first, dict):
            return first.get("id")
    return None
