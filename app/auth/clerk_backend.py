"""Thin async wrapper over Clerk's Backend REST API.

Slice T-5 needs to provision Clerk Organizations programmatically from the
backend (one per Slack workspace), add/remove members, and look up users
by email. The official Python SDK is in flux; for our handful of endpoints
direct httpx calls against the documented REST API are simpler and
vendor-locked-in less.

All calls authenticate with `CLERK_SECRET_KEY` (already in
app.config.Settings.clerk_secret_key). Errors raise `ClerkApiError` with
the upstream message + status so callers can surface clearly.

Base URL: https://api.clerk.com/v1
Docs: https://clerk.com/docs/reference/backend-api
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)


CLERK_API_BASE = "https://api.clerk.com/v1"


class ClerkApiError(Exception):
    """Raised when Clerk's backend returns a non-2xx. Wraps the upstream
    status code + body so callers can branch on it (e.g. 422 on
    duplicate slug, 404 on missing org)."""

    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body
        super().__init__(f"Clerk API {status}: {body}")


def _auth_headers() -> dict[str, str]:
    key = get_settings().clerk_secret_key
    if not key:
        raise ClerkApiError(500, "CLERK_SECRET_KEY not set")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


async def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    """Issue a single Clerk Backend API call. `path` is the absolute path
    starting with /v1 OR the path under /v1 (we prepend the base if it
    doesn't start with http). Returns the parsed JSON body on success."""
    url = path if path.startswith("http") else f"{CLERK_API_BASE}{path}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.request(
            method, url, headers=_auth_headers(), **kwargs
        )
    if resp.status_code >= 400:
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = resp.text
        log.warning("clerk_api_error", method=method, path=path, status=resp.status_code, body=body)
        raise ClerkApiError(resp.status_code, body)
    if not resp.content:
        return {}
    return resp.json()


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #


async def find_user_by_email(email: str) -> dict[str, Any] | None:
    """Return the first Clerk user matching the given email (case-insensitive
    inside Clerk's index). Returns None if no user has registered with that
    email yet -- the typical case when a Slack installer has not yet signed
    into the web app."""
    needle = (email or "").strip()
    if not needle:
        return None
    data = await _request(
        "GET", "/users",
        params={"email_address": [needle], "limit": 1},
    )
    users = data if isinstance(data, list) else data.get("data", [])
    if not users:
        return None
    return users[0]


async def get_user(clerk_user_id: str) -> dict[str, Any]:
    return await _request("GET", f"/users/{clerk_user_id}")


# --------------------------------------------------------------------------- #
# Organizations
# --------------------------------------------------------------------------- #


async def create_organization(
    *,
    name: str,
    created_by: str,
    slug: str | None = None,
    public_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a Clerk Organization owned by `created_by` (Clerk user id).
    Returns the org dict (with `id`, `slug`, etc.).

    Clerk requires the creator to be a real Clerk user. If the Slack
    installer has no Clerk user yet, the caller should defer this until
    they sign up (or pre-create the org with a different owner via
    `created_by_user_id` we set ourselves, then transfer ownership).
    """
    payload: dict[str, Any] = {"name": name, "created_by": created_by}
    if slug:
        payload["slug"] = slug
    if public_metadata is not None:
        payload["public_metadata"] = public_metadata
    return await _request("POST", "/organizations", json=payload)


async def get_organization(clerk_org_id: str) -> dict[str, Any]:
    return await _request("GET", f"/organizations/{clerk_org_id}")


async def update_organization(
    clerk_org_id: str,
    *,
    name: str | None = None,
    public_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if public_metadata is not None:
        payload["public_metadata"] = public_metadata
    return await _request("PATCH", f"/organizations/{clerk_org_id}", json=payload)


async def delete_organization(clerk_org_id: str) -> None:
    await _request("DELETE", f"/organizations/{clerk_org_id}")


# --------------------------------------------------------------------------- #
# Organization Memberships
# --------------------------------------------------------------------------- #


async def add_org_member(
    clerk_org_id: str,
    *,
    user_id: str,
    role: str = "org:member",
) -> dict[str, Any]:
    """Add `user_id` to `clerk_org_id` with the given role. Clerk's standard
    roles are 'org:admin' and 'org:member'; Misterr maps Owner -> org:admin
    and Member -> org:member (the install slice creates the first member
    as org:admin so they can invite teammates)."""
    return await _request(
        "POST",
        f"/organizations/{clerk_org_id}/memberships",
        json={"user_id": user_id, "role": role},
    )


async def remove_org_member(clerk_org_id: str, user_id: str) -> None:
    await _request(
        "DELETE", f"/organizations/{clerk_org_id}/memberships/{user_id}"
    )


async def list_org_members(
    clerk_org_id: str, *, limit: int = 100
) -> list[dict[str, Any]]:
    """Returns all org memberships (paginated server-side; we just fetch
    `limit` and call it good for now -- Slack workspaces of >100 users
    aren't the target for v1)."""
    data = await _request(
        "GET",
        f"/organizations/{clerk_org_id}/memberships",
        params={"limit": limit},
    )
    items = data if isinstance(data, list) else data.get("data", [])
    return items


async def find_org_membership(
    clerk_org_id: str, user_id: str
) -> dict[str, Any] | None:
    """Returns the membership dict for `user_id` in `clerk_org_id`, or
    None if they're not a member. Lighter than `list_org_members` for
    point-checks (still uses pagination since Clerk doesn't expose a
    direct `?user_id=...` filter)."""
    members = await list_org_members(clerk_org_id, limit=200)
    for m in members:
        if (m.get("public_user_data") or {}).get("user_id") == user_id:
            return m
    return None


# --------------------------------------------------------------------------- #
# Org invitations
# --------------------------------------------------------------------------- #


async def create_org_invitation(
    clerk_org_id: str,
    *,
    email_address: str,
    inviter_user_id: str,
    role: str = "org:member",
    redirect_url: str | None = None,
) -> dict[str, Any]:
    """Send a Clerk-hosted invitation email. The recipient clicks the link,
    signs up (or signs in), and gets auto-added to the org."""
    payload: dict[str, Any] = {
        "email_address": email_address,
        "inviter_user_id": inviter_user_id,
        "role": role,
    }
    if redirect_url:
        payload["redirect_url"] = redirect_url
    return await _request(
        "POST",
        f"/organizations/{clerk_org_id}/invitations",
        json=payload,
    )


__all__ = [
    "ClerkApiError",
    "CLERK_API_BASE",
    # Users
    "find_user_by_email",
    "get_user",
    # Organizations
    "create_organization",
    "get_organization",
    "update_organization",
    "delete_organization",
    # Memberships
    "add_org_member",
    "remove_org_member",
    "list_org_members",
    "find_org_membership",
    # Invitations
    "create_org_invitation",
]
