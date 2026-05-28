"""ConvexSharedSpaceBackend: writes Space lifecycle to a single shared Convex
deployment via its HTTP API. Logical multi-tenancy by `space_id` (every row
in Convex carries it; assertSpaceAccess guards every read).

Auth uses Convex's admin key (set in Doppler; not exposed to the model, the
sandbox, or the browser). The refresh action inside Convex calls our
`/internal/spaces/refresh` endpoint with a separate shared token; both secrets
live only in Doppler / Convex env.

Migration path: when Convex Management API (or Components GA) lets us
provision per-Space deployments, a new ConvexProjectSpaceBackend slots in
behind the same SpaceBackend interface; this impl stays as the fallback.
"""

from __future__ import annotations

import uuid
from typing import Any

import aiohttp
import structlog

from app.config import get_settings
from app.spaces.backend import SpaceBackend, SpaceDeployment
from app.spaces.clerk import resolve_email_to_user_id

log = structlog.get_logger(__name__)


class ConvexSharedSpaceBackend(SpaceBackend):
    """One shared Convex deployment, multi-tenant by space_id."""

    name = "convex-shared"

    def __init__(self, convex_url: str, deploy_key: str, hosting_site_url: str | None) -> None:
        # Normalise to no trailing slash to keep request URLs predictable.
        self._base = convex_url.rstrip("/")
        self._deploy_key = deploy_key
        # The site URL (where Convex Hosting serves the frontend) differs from
        # the API URL. e.g. api=https://<slug>.convex.cloud, site=https://<slug>.convex.site
        self._site_url = (hosting_site_url or self._base.replace(".convex.cloud", ".convex.site")).rstrip("/")

    def _frontend_url(self, space_id: uuid.UUID) -> str:
        return f"{self._site_url}/s/{space_id}"

    async def _call(self, kind: str, path: str, args: dict[str, Any]) -> Any:
        """Call a Convex query / mutation / action over HTTP with the admin key.

        `kind` is "query" | "mutation" | "action". Convex returns
        {status: "success", value: ...} or {status: "error", errorMessage: ...}.
        """
        url = f"{self._base}/api/{kind}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Convex {self._deploy_key}",
        }
        body = {"path": path, "args": args, "format": "json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=body) as resp:
                payload = await resp.json()
                if resp.status >= 400:
                    raise RuntimeError(f"Convex {resp.status}: {str(payload)[:300]}")
        if isinstance(payload, dict) and payload.get("status") == "error":
            raise RuntimeError(f"Convex {path} error: {payload.get('errorMessage', '')[:300]}")
        return payload.get("value") if isinstance(payload, dict) else payload

    # ----------------- SpaceBackend impl ----------------------------------- #

    async def deploy(
        self,
        *,
        space_id: uuid.UUID,
        workspace_id: uuid.UUID,
        name: str,
        data_binding: dict,
        access_list: list,
    ) -> SpaceDeployment:
        refresh_interval = int(data_binding.get("refresh_interval") or 60)
        try:
            await self._call(
                "mutation", "spaces:createSpace",
                {
                    "space_id": str(space_id),
                    "workspace_id": str(workspace_id),
                    "name": name,
                    "data_binding": data_binding,
                    "refresh_interval": refresh_interval,
                },
            )
            await self._sync_access(space_id, access_list)
        except Exception as exc:
            log.warning("convex_space_deploy_failed", space_id=str(space_id), error=str(exc))
            raise

        log.info("convex_space_deploy", space_id=str(space_id), name=name, n_access=len(access_list))
        return SpaceDeployment(
            frontend_url=self._frontend_url(space_id),
            convex_project_ref=self._base,
            convex_deployment_ref=str(space_id),  # logical id within the shared deployment
            admin_key_vault_ref=None,
        )

    async def update_binding(self, *, space_id: uuid.UUID, data_binding: dict) -> None:
        args: dict[str, Any] = {
            "space_id": str(space_id),
            "data_binding": data_binding,
        }
        if "refresh_interval" in data_binding:
            args["refresh_interval"] = int(data_binding["refresh_interval"])
        await self._call("mutation", "spaces:updateBinding", args)
        log.info("convex_space_update_binding", space_id=str(space_id))

    async def update_access(self, *, space_id: uuid.UUID, access_list: list) -> None:
        await self._sync_access(space_id, access_list)
        log.info("convex_space_update_access", space_id=str(space_id), n_access=len(access_list))

    async def delete(self, *, space_id: uuid.UUID) -> None:
        # First flip status to "deleted" + wipe snapshots/access. The refresh
        # loop sees status != "active" on its next tick and exits without
        # rescheduling. After that, purgeSpaceConfig drops the tombstone row.
        await self._call("mutation", "spaces:deleteSpace", {"space_id": str(space_id)})
        try:
            await self._call("mutation", "spaces:purgeSpaceConfig", {"space_id": str(space_id)})
        except Exception as exc:  # noqa: BLE001
            # Purge is best-effort -- if it lands before the next refresh tick
            # we may race; the tombstone row is harmless either way.
            log.warning("convex_space_purge_failed", space_id=str(space_id), error=str(exc))
        log.info("convex_space_delete", space_id=str(space_id))

    # ----------------- helpers -------------------------------------------- #

    async def _sync_access(self, space_id: uuid.UUID, access_list: list) -> None:
        """Translate access entries into Convex rows with Clerk user_ids
        resolved at deploy time. Fallback to pending (empty user_id + email
        stored) when the email isn't yet registered in Clerk -- the Convex
        side patches it lazily on the first login that matches.

        Each entry is `{email?, user_id?, clerk_user_id?, role?}`. We always
        store `email` if present so the lazy fallback can claim it later."""
        entries = []
        for e in access_list or []:
            if not isinstance(e, dict):
                continue
            email = (e.get("email") or "").strip().lower() or None
            uid = e.get("user_id") or e.get("clerk_user_id")
            # If the caller didn't pass a Clerk user_id, try to resolve from
            # email. Network/missing-secret/unregistered -> empty (pending).
            if not uid and email:
                uid = await resolve_email_to_user_id(email)
            if not uid and not email:
                continue  # nothing to anchor the row to
            entries.append({
                "user_id": str(uid) if uid else "",  # "" means pending
                "email": email,
                "role": e.get("role"),
            })
        await self._call(
            "mutation", "spaces:replaceAccess",
            {"space_id": str(space_id), "entries": entries},
        )
