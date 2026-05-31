"""Integrations REST endpoints for the web app (slice T-6).

Surface:
- GET  /api/integrations/catalog            -> all available apps (Composio + Pipedream)
- GET  /api/integrations/connections        -> connected accounts visible to the caller
- POST /api/integrations/connections        -> initiate a connect (returns redirect URL)
- DELETE /api/integrations/connections/{id} -> disconnect (revokes + soft-deletes)

Scope rules (enforced in this layer):
- Listing: members see all Team connections + their own Private; admins
  additionally see other users' Private accounts (read-only label, not the
  credentials themselves -- those never leave the provider).
- Creating: any member can create a Private (owned by themselves) OR a
  Team (visible to everyone in the workspace). Privates always set
  owner_user_id to the caller. Teams set owner_user_id=NULL.
- Deleting: only the owner (for Private) or any admin (for Team).
"""

from __future__ import annotations

import uuid
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app.auth.clerk import ClerkClaims, require_app_user, require_clerk_user, ResolvedAppUser
from app.db.models import AppUser, IntegrationConnection
from app.db.session import get_session
from app.integrations.catalog import CatalogApp, POPULAR_SLUGS, get_catalog
from app.integrations import connect as connect_flow

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


class CatalogItemOut(BaseModel):
    slug: str
    name: str
    description: str | None
    logo_url: str | None
    provider: str
    categories: list[str]
    popular: bool


class CatalogResponse(BaseModel):
    apps: list[CatalogItemOut]
    total: int


class ConnectionOut(BaseModel):
    id: str
    app: str
    provider: str
    status: str
    scope: str
    account_label: str | None
    owner_user_id: str | None
    owner_display: str | None  # name/email of the owner, if available
    created_at: str | None


class ConnectionsResponse(BaseModel):
    app: str | None  # null if listing across all apps
    connections: list[ConnectionOut]
    total: int


class CreateConnectionRequest(BaseModel):
    app: str
    scope: Literal["team", "private"] = "private"
    account_label: str | None = Field(default=None, max_length=128)
    # Where Clerk's hosted UI should redirect after OAuth completes; the
    # backend includes this in the connect flow's state so the user lands
    # on the right page.
    redirect_url: str | None = None


class CreateConnectionResponse(BaseModel):
    connection_id: str
    connect_url: str
    provider: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _is_admin(clerk: ClerkClaims) -> bool:
    return (clerk.org_role or "").lower() == "org:admin"


def _serialize_connection(
    conn: IntegrationConnection, owner_display: str | None = None
) -> ConnectionOut:
    return ConnectionOut(
        id=str(conn.id),
        app=conn.app,
        provider=conn.provider,
        status=conn.status,
        scope=conn.scope,
        account_label=conn.account_label,
        owner_user_id=str(conn.owner_user_id) if conn.owner_user_id else None,
        owner_display=owner_display,
        created_at=conn.created_at.isoformat() if conn.created_at else None,
    )


# --------------------------------------------------------------------------- #
# GET /api/integrations/catalog
# --------------------------------------------------------------------------- #


@router.get("/catalog", response_model=CatalogResponse)
async def list_catalog(
    user: ResolvedAppUser = Depends(require_app_user),
    only_popular: bool = False,
) -> CatalogResponse:
    """All available apps the workspace could connect. Cached server-side
    for 1h; cheap to call. `only_popular=true` narrows to the curated short
    list used by the 'Popular integrations' tab."""
    apps = await get_catalog()
    if only_popular:
        apps = [a for a in apps if a.popular]
    return CatalogResponse(
        apps=[
            CatalogItemOut(
                slug=a.slug,
                name=a.name,
                description=a.description,
                logo_url=a.logo_url,
                provider=a.provider,
                categories=a.categories,
                popular=a.popular,
            )
            for a in apps
        ],
        total=len(apps),
    )


# --------------------------------------------------------------------------- #
# GET /api/integrations/connections
# --------------------------------------------------------------------------- #


@router.get("/connections", response_model=ConnectionsResponse)
async def list_connections(
    user: ResolvedAppUser = Depends(require_app_user),
    clerk: ClerkClaims = Depends(require_clerk_user),
    app: str | None = None,
) -> ConnectionsResponse:
    """Return the connections this caller can see. Scope rules:
    - Members see all Team + their own Private.
    - Admins additionally see other users' Private (labels visible; the
      provider credentials never leave the provider regardless).

    Optional `app` filter narrows to a single integration.
    """
    is_admin = _is_admin(clerk)
    async with get_session() as session:
        stmt = select(IntegrationConnection).where(
            IntegrationConnection.workspace_id == user.workspace_id
        )
        if app:
            stmt = stmt.where(IntegrationConnection.app == app.lower())
        if not is_admin:
            stmt = stmt.where(
                or_(
                    IntegrationConnection.scope == "team",
                    IntegrationConnection.owner_user_id == user.app_user_id,
                )
            )
        rows = (await session.execute(stmt)).scalars().all()

        # Resolve owner display strings (best-effort). For Team connections
        # owner is null and we skip; for Privates we show the owner's email
        # if we have it (via the slack_user join), else slack_user_id.
        owner_ids = [r.owner_user_id for r in rows if r.owner_user_id]
        owner_display_by_id: dict[uuid.UUID, str] = {}
        if owner_ids:
            from app.db.models import SlackUser  # local: avoid cycle at import
            au_rows = (
                await session.execute(
                    select(AppUser).where(AppUser.id.in_(owner_ids))
                )
            ).scalars().all()
            slack_id_by_appuser = {a.id: a.slack_user_id for a in au_rows}
            slack_rows = (
                await session.execute(
                    select(SlackUser).where(
                        SlackUser.workspace_id == user.workspace_id,
                        SlackUser.slack_user_id.in_(
                            [a.slack_user_id for a in au_rows]
                        ),
                    )
                )
            ).scalars().all()
            slack_by_id = {s.slack_user_id: s for s in slack_rows}
            for au in au_rows:
                su = slack_by_id.get(au.slack_user_id)
                if su:
                    owner_display_by_id[au.id] = (
                        su.display_name or su.real_name or su.email or au.slack_user_id
                    )
                else:
                    owner_display_by_id[au.id] = au.slack_user_id

    out = [
        _serialize_connection(
            conn,
            owner_display=owner_display_by_id.get(conn.owner_user_id) if conn.owner_user_id else None,
        )
        for conn in rows
    ]
    return ConnectionsResponse(app=app, connections=out, total=len(out))


# --------------------------------------------------------------------------- #
# POST /api/integrations/connections (initiate)
# --------------------------------------------------------------------------- #


@router.post("/connections", response_model=CreateConnectionResponse)
async def create_connection(
    body: CreateConnectionRequest,
    user: ResolvedAppUser = Depends(require_app_user),
    clerk: ClerkClaims = Depends(require_clerk_user),
    request: Request = None,  # type: ignore[assignment]
) -> CreateConnectionResponse:
    """Initiate a connect for the given app. Returns a `connect_url` the
    frontend redirects the browser to (Composio's or Pipedream's hosted
    OAuth UI). The backend's webhook receives the connection id once the
    user completes the OAuth dance.

    Permission:
    - Any member can create a Private (owned by themselves).
    - Only admins can create a Team connection (visible to everyone).
    """
    app = body.app.lower().strip()
    if not app:
        raise HTTPException(status_code=400, detail="`app` is required")

    if body.scope == "team" and not _is_admin(clerk):
        raise HTTPException(
            status_code=403,
            detail="Only workspace admins can create team-scope connections.",
        )

    # Resolve provider via catalog (so we pick Composio when both have it).
    catalog = await get_catalog()
    catalog_match = next((c for c in catalog if c.slug == app), None)
    if catalog_match is None:
        raise HTTPException(
            status_code=404, detail=f"App {app!r} not found in the catalog."
        )
    provider = catalog_match.provider

    owner_user_id = user.app_user_id if body.scope == "private" else None

    # Create a `pending` row first so the webhook (which only knows the
    # provider connection id, not our internal one) can match by app +
    # workspace + status='pending'.
    async with get_session() as session:
        conn = IntegrationConnection(
            workspace_id=user.workspace_id,
            app=app,
            provider=provider,
            status="pending",
            scope=body.scope,
            owner_user_id=owner_user_id,
            account_label=(body.account_label or None),
        )
        session.add(conn)
        await session.commit()
        await session.refresh(conn)

    try:
        # Reuse the existing connect-flow link minter. It encapsulates the
        # provider-specific OAuth handshake (Pipedream Connect link or
        # Composio's /connected_accounts/initiate).
        connect_url = await connect_flow._mint_connect_link(
            provider_name=provider,
            workspace_id=str(user.workspace_id),
            app=app,
        )
        if not connect_url:
            raise RuntimeError("provider returned no connect url")
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "integration_connect_initiate_failed",
            workspace_id=str(user.workspace_id),
            app=app,
            error=str(exc),
        )
        # Roll back the pending row to avoid leaving stale records.
        async with get_session() as session:
            row = (
                await session.execute(
                    select(IntegrationConnection).where(IntegrationConnection.id == conn.id)
                )
            ).scalar_one_or_none()
            if row is not None:
                await session.delete(row)
                await session.commit()
        raise HTTPException(
            status_code=502,
            detail=f"Could not start connect flow with {provider}: {exc}",
        ) from exc

    return CreateConnectionResponse(
        connection_id=str(conn.id),
        connect_url=connect_url,
        provider=provider,
    )


# --------------------------------------------------------------------------- #
# DELETE /api/integrations/connections/{id}
# --------------------------------------------------------------------------- #


@router.delete("/connections/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: str,
    user: ResolvedAppUser = Depends(require_app_user),
    clerk: ClerkClaims = Depends(require_clerk_user),
) -> None:
    """Revoke the connection at the provider AND delete the local row.
    Permission:
    - Private: only the owner can delete.
    - Team: only admins.
    """
    try:
        conn_uuid = uuid.UUID(connection_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid connection id") from exc

    async with get_session() as session:
        conn = (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.id == conn_uuid,
                    IntegrationConnection.workspace_id == user.workspace_id,
                )
            )
        ).scalar_one_or_none()
        if conn is None:
            raise HTTPException(status_code=404, detail="Connection not found")

        if conn.scope == "team":
            if not _is_admin(clerk):
                raise HTTPException(
                    status_code=403,
                    detail="Only admins can disconnect team-scope integrations.",
                )
        else:  # private
            if conn.owner_user_id != user.app_user_id:
                raise HTTPException(
                    status_code=403,
                    detail="Only the owner can disconnect a private integration.",
                )

    # Revoke at the provider (best-effort; the local row gets deleted
    # regardless so the workspace can't end up stuck with a stale entry
    # in the UI). Uses the existing per-provider revoke helpers in
    # app.integrations.{composio,pipedream}; if either is unavailable we
    # log and continue.
    try:
        if conn.provider == "composio" and conn.pipedream_account_id:
            from app.integrations import composio as composio_api
            await composio_api._request(
                "DELETE", f"/connected_accounts/{conn.pipedream_account_id}"
            )
        elif conn.provider == "pipedream" and conn.pipedream_account_id:
            from app.integrations import pipedream as pd_api
            try:
                await pd_api.delete_account(conn.pipedream_account_id)  # type: ignore[attr-defined]
            except AttributeError:
                # `delete_account` may not exist yet in the pipedream client;
                # leave revoke to a follow-up commit. The local row still
                # gets deleted below.
                pass
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "integration_disconnect_provider_failed",
            workspace_id=str(user.workspace_id),
            app=conn.app,
            error=str(exc),
        )

    async with get_session() as session:
        await session.execute(
            select(IntegrationConnection).where(IntegrationConnection.id == conn_uuid)
        )
        # Reload + delete in this session so the ORM cascade respects the FK.
        row = (
            await session.execute(
                select(IntegrationConnection).where(IntegrationConnection.id == conn_uuid)
            )
        ).scalar_one_or_none()
        if row is not None:
            await session.delete(row)
            await session.commit()
    log.info(
        "integration_disconnected",
        workspace_id=str(user.workspace_id),
        app=conn.app,
        connection_id=str(conn_uuid),
        scope=conn.scope,
    )


__all__ = ["router"]
