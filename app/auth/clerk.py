"""Clerk JWT verification for the Misterr web app (slice T-2).

The web sends `Authorization: Bearer <JWT>` on every API call. We verify the
JWT against Clerk's JWKS endpoint (RS256), cache the JWKS for an hour (it's
small and stable), and map the verified Clerk identity to our internal
`AppUser` by email + (optional) workspace selection.

Why JWKS over Clerk's own SDK: Clerk's Python SDK is in flux; pyjose against
a public JWKS endpoint is widely-supported, easy to test, and avoids vendor
lock-in if we ever swap auth providers.

The `require_app_user` Depends returns a tuple `(workspace_id, app_user_id)`
that the API handlers pass directly to the repository -- we do NOT set
contextvars here. T-1's repository takes workspace_id as an explicit argument,
which is cleaner for request-scoped logic and avoids accidental leakage
between concurrent requests.

Multi-workspace users: when a Clerk email maps to AppUsers in more than one
workspace, the request MUST include `X-Misterr-Workspace-Id`. We surface a
400 with a structured payload so the frontend can prompt the user to pick.
For single-workspace users the header is optional.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt
from jose.exceptions import JWKError
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import AppUser, SlackUser, Workspace
from app.db.session import get_session

log = structlog.get_logger(__name__)


# JWKS is a small JSON document (a few keys). Refresh hourly: Clerk rotates
# rarely and a cached miss is just one HTTP round-trip on the next request.
_JWKS_TTL_SECONDS = 3600
_jwks_cache: dict[str, Any] | None = None
_jwks_cache_expires_at: float = 0.0
_jwks_url_cached: str | None = None


@dataclass(frozen=True)
class ClerkClaims:
    """Validated Clerk JWT payload. We only surface the few fields we need;
    the full token is intentionally not propagated to handlers."""

    sub: str  # Clerk user id (e.g. "user_2abc...")
    email: str | None
    # Clerk org context: present only when the session has an "active
    # organization" AND the JWT template emits these claims (the "backend"
    # template under JWT Templates in Clerk dashboard must include
    # `org_id`, `org_role`, `org_slug` -- see app/auth/README.md).
    org_id: str | None
    org_role: str | None
    org_slug: str | None
    raw: dict[str, Any]


def _jwks_url() -> str:
    settings = get_settings()
    if settings.clerk_jwks_url:
        return settings.clerk_jwks_url
    issuer = (settings.clerk_jwt_issuer or "").rstrip("/")
    if not issuer:
        raise HTTPException(
            status_code=500,
            detail="server misconfigured: neither CLERK_JWKS_URL nor CLERK_JWT_ISSUER set",
        )
    return f"{issuer}/.well-known/jwks.json"


async def _fetch_jwks() -> dict[str, Any]:
    """Fetch Clerk's JWKS document. Cached in-process for _JWKS_TTL_SECONDS.
    On a transient HTTP failure the cache (if any) is returned to avoid
    cascading 401s during a brief Clerk outage."""
    global _jwks_cache, _jwks_cache_expires_at, _jwks_url_cached

    url = _jwks_url()
    now = time.monotonic()
    # If URL changed between calls (config reload), invalidate.
    if _jwks_cache is None or _jwks_cache_expires_at <= now or _jwks_url_cached != url:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                _jwks_cache = resp.json()
                _jwks_url_cached = url
                _jwks_cache_expires_at = now + _JWKS_TTL_SECONDS
        except Exception as exc:  # noqa: BLE001
            if _jwks_cache is None:
                log.warning("clerk_jwks_fetch_failed_no_cache", error=str(exc))
                raise HTTPException(
                    status_code=503, detail=f"could not fetch JWKS: {exc}"
                ) from exc
            log.warning(
                "clerk_jwks_fetch_failed_using_stale_cache",
                error=str(exc),
            )
    return _jwks_cache  # type: ignore[return-value]


def _reset_jwks_cache_for_test() -> None:
    """Test-only hook: clear the JWKS cache so a test that patches httpx
    doesn't see a stale value from an earlier test."""
    global _jwks_cache, _jwks_cache_expires_at, _jwks_url_cached
    _jwks_cache = None
    _jwks_cache_expires_at = 0.0
    _jwks_url_cached = None


async def verify_clerk_jwt(token: str) -> ClerkClaims:
    """Verify a Clerk-issued JWT against the JWKS and return the parsed claims.

    Raises HTTPException(401) on any verification error. Specific failure modes
    (expired, wrong signature, wrong key, malformed token) all collapse to
    a single 401 to avoid leaking internals via the error message.
    """
    if not token:
        raise HTTPException(status_code=401, detail="empty token")

    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="malformed token") from exc

    kid = unverified_header.get("kid")
    if not kid:
        raise HTTPException(status_code=401, detail="token missing kid header")

    jwks = await _fetch_jwks()
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if key is None:
        # Possible JWKS rotation: refresh once + retry.
        global _jwks_cache_expires_at
        _jwks_cache_expires_at = 0.0
        jwks = await _fetch_jwks()
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if key is None:
            raise HTTPException(status_code=401, detail="unknown signing key")

    settings = get_settings()
    issuer = (settings.clerk_jwt_issuer or "").rstrip("/") or None

    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=[key.get("alg", "RS256")],
            issuer=issuer,
            # We don't pin `audience` here. Clerk's session tokens default to
            # no `aud`; if you turn on custom JWT templates, set them to omit
            # the aud or add validation here.
            options={"verify_aud": False},
        )
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}") from exc
    except JWKError as exc:
        raise HTTPException(status_code=401, detail=f"key error: {exc}") from exc

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="token missing sub")

    # Clerk session tokens include the primary email under different keys
    # depending on the template: `email`, `primary_email_address`, or
    # `email_address`. Try each.
    email = (
        payload.get("email")
        or payload.get("primary_email_address")
        or payload.get("email_address")
    )
    # Org context: present in default Clerk session tokens when the user has
    # an active organization; in templated tokens only if the template
    # explicitly emits these claims (see Clerk dashboard -> JWT Templates).
    org_id = payload.get("org_id") or payload.get("organization_id")
    org_role = payload.get("org_role") or payload.get("organization_role")
    org_slug = payload.get("org_slug") or payload.get("organization_slug")
    return ClerkClaims(
        sub=sub, email=email, org_id=org_id, org_role=org_role,
        org_slug=org_slug, raw=payload,
    )


async def require_clerk_user(
    authorization: str = Header(..., alias="Authorization"),
) -> ClerkClaims:
    """Depends entry point: extract Bearer token and verify it."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401, detail="Authorization header must be 'Bearer <jwt>'"
        )
    token = authorization.split(" ", 1)[1].strip()
    return await verify_clerk_jwt(token)


# --------------------------------------------------------------------------- #
# Email -> AppUser resolution (workspace-aware)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ResolvedAppUser:
    """Result of mapping a Clerk session to one internal AppUser. Always
    bound to a single workspace_id (the caller picked it via header or there
    was only one option)."""

    workspace_id: uuid.UUID
    app_user_id: uuid.UUID
    clerk_user_id: str
    email: str


async def _candidate_app_users_for_email(email: str) -> list[AppUser]:
    """Find every AppUser in the system whose workspace contains a SlackUser
    with this email (case-insensitive). One Clerk user -> potentially many
    AppUsers if they're in multiple Slack workspaces."""
    needle = (email or "").strip().lower()
    if not needle:
        return []
    async with get_session() as session:
        slack_users = (
            await session.execute(
                select(SlackUser).where(
                    func.lower(SlackUser.email) == needle,
                    SlackUser.deleted == False,  # noqa: E712
                )
            )
        ).scalars().all()
        if not slack_users:
            return []
        # For each (workspace_id, slack_user_id) hit, find the matching AppUser.
        # Workspaces where the user is in the cached roster but never DM'd /
        # mentioned Misterr will not yet have an AppUser row -- skip those
        # transparently.
        pairs = [(su.workspace_id, su.slack_user_id) for su in slack_users]
        results: list[AppUser] = []
        for ws_id, sk_id in pairs:
            row = (
                await session.execute(
                    select(AppUser).where(
                        AppUser.workspace_id == ws_id,
                        AppUser.slack_user_id == sk_id,
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                results.append(row)
        return results


async def _resolve_via_org(
    clerk: ClerkClaims,
) -> ResolvedAppUser | None:
    """New org-based resolution: when the Clerk session has an active
    organization, the JWT carries `org_id`. We map that 1:1 to a Workspace
    row (via the `clerk_org_id` column populated at install / backfill
    time) and then locate the AppUser by `(workspace_id, clerk_user_id)`.

    Returns None to mean "let the caller fall back to the legacy
    email-match path". The org-based path is best-effort: if the
    user's active Clerk org isn't yet linked to a workspace (fresh
    install racing the provisioning, or the user is sitting in a
    brand-new Clerk org that has no Slack workspace), don't 404 --
    that wedges the inner pages even when the user has perfectly
    valid email-based access to OTHER workspaces. Returning None
    here lets `resolve_app_user_from_clerk` try the email path,
    which is much more permissive and covers the common case
    (invited members whose Clerk email matches their Slack email).

    Raises HTTPException ONLY on the genuine "you have an AppUser
    here but Clerk identity doesn't match" case (handled below).
    """
    if not clerk.org_id:
        return None

    async with get_session() as session:
        ws = (
            await session.execute(
                select(Workspace).where(Workspace.clerk_org_id == clerk.org_id)
            )
        ).scalar_one_or_none()
        if ws is None:
            # Fall back to the email-match path. Logging for visibility:
            # if this fires often we want to know which orgs need
            # backfilling.
            log.info(
                "clerk_org_unknown_fallback_to_email",
                clerk_org_id=clerk.org_id,
                clerk_user=clerk.sub,
                email=clerk.email,
            )
            return None

        # Direct hit: clerk_user_id already linked.
        app_user = (
            await session.execute(
                select(AppUser).where(
                    AppUser.workspace_id == ws.id,
                    AppUser.clerk_user_id == clerk.sub,
                )
            )
        ).scalar_one_or_none()

        # Lazy link: not linked yet, but the user's email matches a
        # SlackUser in this workspace and that SlackUser has an AppUser.
        # Bind them so subsequent requests are O(1).
        if app_user is None and clerk.email:
            needle = clerk.email.strip().lower()
            slack_user = (
                await session.execute(
                    select(SlackUser).where(
                        SlackUser.workspace_id == ws.id,
                        func.lower(SlackUser.email) == needle,
                        SlackUser.deleted == False,  # noqa: E712
                    )
                )
            ).scalar_one_or_none()
            if slack_user is not None:
                app_user = (
                    await session.execute(
                        select(AppUser).where(
                            AppUser.workspace_id == ws.id,
                            AppUser.slack_user_id == slack_user.slack_user_id,
                        )
                    )
                ).scalar_one_or_none()
                if app_user is not None and app_user.clerk_user_id != clerk.sub:
                    app_user.clerk_user_id = clerk.sub
                    await session.commit()
                    log.info(
                        "clerk_user_lazily_linked",
                        app_user_id=str(app_user.id),
                        clerk_user_id=clerk.sub,
                        workspace_id=str(ws.id),
                    )

        if app_user is None:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Your Clerk account is in this organization but has not "
                    "interacted with Misterr in Slack yet. Send the bot any "
                    "DM in Slack to finish setup, then refresh."
                ),
            )

        return ResolvedAppUser(
            workspace_id=ws.id,
            app_user_id=app_user.id,
            clerk_user_id=clerk.sub,
            email=clerk.email or "",
        )


def _platform_admin_emails() -> set[str]:
    raw = (get_settings().platform_admins or "").strip()
    if not raw:
        return set()
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_platform_admin_email(email: str | None) -> bool:
    """Case-insensitive membership check against the PLATFORM_ADMINS env var.
    Returns False for None/empty emails so an unauthenticated path can use
    it as a guard without an extra None check."""
    if not email:
        return False
    return email.strip().lower() in _platform_admin_emails()


async def require_platform_admin(
    clerk: ClerkClaims = Depends(require_clerk_user),
) -> ClerkClaims:
    """Gate /admin/* endpoints on the PLATFORM_ADMINS env var. The Clerk JWT
    must verify (require_clerk_user) AND the email must match an entry in
    the env list. Returns the ClerkClaims so the handler has the admin's
    identity for logging.

    NO workspace resolution here: an admin operates across workspaces, so
    we don't shove them through `require_app_user` (which insists on a
    single workspace binding). Admin endpoints take workspace_id explicitly
    where they need scope."""
    if not is_platform_admin_email(clerk.email):
        # Don't leak whether the email exists in the system; just deny.
        raise HTTPException(
            status_code=403, detail="not a platform admin"
        )
    return clerk


async def require_app_user(
    clerk: ClerkClaims = Depends(require_clerk_user),
    x_misterr_workspace_id: str | None = Header(
        default=None, alias="X-Misterr-Workspace-Id"
    ),
) -> ResolvedAppUser:
    """Map a verified Clerk JWT to a single AppUser.

    Resolution order:
      1. If the JWT has an `org_id` claim, use the org-based lookup
         (`Workspace.clerk_org_id`). This is the post-migration path and
         the source of truth going forward.
      2. Else, legacy email-match path: find SlackUsers with the same
         email, narrow by `X-Misterr-Workspace-Id` if multiple. Kept
         during the transition window so users whose session still
         predates the org rollout don't get locked out.
    """
    # Preferred path once the org-migration backfill has run.
    via_org = await _resolve_via_org(clerk)
    if via_org is not None:
        return via_org

    # Legacy fallback: no `org_id` claim. Logged so we can audit how often
    # this still fires and decide when to remove it.
    log.info(
        "auth_resolve_via_email_fallback",
        clerk_user_id=clerk.sub,
        email=clerk.email,
    )

    if not clerk.email:
        raise HTTPException(
            status_code=403,
            detail="Clerk token has no email claim; cannot map to a workspace.",
        )

    candidates = await _candidate_app_users_for_email(clerk.email)
    if not candidates:
        raise HTTPException(
            status_code=403,
            detail=(
                "No Misterr workspace is linked to this Clerk email. "
                "Install Misterr in your Slack workspace, then sign in again."
            ),
        )

    if x_misterr_workspace_id:
        try:
            ws_uuid = uuid.UUID(x_misterr_workspace_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="invalid X-Misterr-Workspace-Id"
            ) from exc
        match = next((c for c in candidates if c.workspace_id == ws_uuid), None)
        if match is None:
            raise HTTPException(
                status_code=403,
                detail="Clerk user is not a member of the requested workspace.",
            )
        return ResolvedAppUser(
            workspace_id=match.workspace_id,
            app_user_id=match.id,
            clerk_user_id=clerk.sub,
            email=clerk.email,
        )

    if len(candidates) > 1:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "multiple_workspaces",
                "message": (
                    "Your Clerk email is linked to multiple Misterr workspaces. "
                    "Include X-Misterr-Workspace-Id with the request."
                ),
                "workspaces": [str(c.workspace_id) for c in candidates],
            },
        )

    only = candidates[0]
    return ResolvedAppUser(
        workspace_id=only.workspace_id,
        app_user_id=only.id,
        clerk_user_id=clerk.sub,
        email=clerk.email,
    )


__all__ = [
    "ClerkClaims",
    "ResolvedAppUser",
    "verify_clerk_jwt",
    "require_clerk_user",
    "require_app_user",
    "require_platform_admin",
    "is_platform_admin_email",
    "_reset_jwks_cache_for_test",
]
