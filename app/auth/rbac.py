"""Role-based access control helpers for FastAPI routers.

Source of truth: the Clerk Organization role baked into the JWT (`org_role`
claim, parsed by `verify_clerk_jwt`). Two values are meaningful:

  - `org:admin` -> can change billing, invite/remove teammates, disconnect
    team-scope integrations, promote/demote other members.
  - `org:member` -> can do everything else (create skills, run agent, view
    usage, see roster).

Why Clerk roles instead of a parallel DB column:
  - Avoids duplicating state that has to be kept in sync (every Clerk role
    change would need a mirror write).
  - The role is already signed into the JWT, so every authenticated request
    carries it for free.
  - The installer is set as `org:admin` by `add_org_member` in the install
    slice, so we never have a workspace with zero admins.

Failure modes:
  - JWT without an `org_role` claim -> treated as non-admin (403). This
    happens for accounts that haven't been provisioned into an org yet.
  - JWT with an unknown role string -> treated as non-admin. We don't
    enumerate the role set anywhere, so anything that isn't `org:admin`
    falls through.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException

from app.auth.clerk import ClerkClaims, require_clerk_user


_ADMIN_ROLE = "org:admin"


def is_admin(clerk: ClerkClaims) -> bool:
    return (clerk.org_role or "").lower() == _ADMIN_ROLE


async def require_workspace_admin(
    clerk: ClerkClaims = Depends(require_clerk_user),
) -> ClerkClaims:
    """FastAPI Depends that 403s non-admin callers. Returns the parsed
    ClerkClaims so handlers can still read `clerk.sub`, `clerk.email`, etc.

    Compose with `require_app_user` when the handler also needs the
    workspace_id / app_user_id mapping:

        @router.post("/admin-only")
        async def foo(
            user: ResolvedAppUser = Depends(require_app_user),
            clerk: ClerkClaims = Depends(require_workspace_admin),
        ):
            ...
    """
    if not is_admin(clerk):
        raise HTTPException(
            status_code=403,
            detail="Only workspace admins can perform this action.",
        )
    return clerk


__all__ = ["is_admin", "require_workspace_admin"]
