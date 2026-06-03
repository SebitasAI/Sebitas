"""PipedreamProvider: today's only implementation of IntegrationProvider.

Wraps the low-level `pipedream` HTTP client. Auth injection is provider-local:
`configured_props[<app>] = {"authProvisionId": account_id}` works for both OAuth
and custom-auth in Pipedream Connect; the connector resolves credentials
server-side either way. Above this layer the gateway sees ONE code path.

KNOWN CONNECTOR LIMITATIONS (not fixable in this layer):
- Metabase: as of 2026-05, the Pipedream connector for Metabase appears to
  accept only session-based auth (cookie from `/api/session`), not the API key
  that newer Metabase versions support. Calls fail with 401 or session-related
  messages. The connector_limitation heuristic in errors.py flags this; the
  real fix is a fallback provider (HTTP-direct to Metabase /api/dataset) which
  is intentionally out of scope here.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from app.integrations import pipedream as pd
from app.integrations.provider import IntegrationError, IntegrationProvider

log = structlog.get_logger(__name__)


# Module-level cache: action_id -> the prop name to use for the auth field.
# Schemas are immutable per action_id; cache lives for the process lifetime.
_AUTH_PROP_CACHE: dict[str, str] = {}


class PipedreamProvider(IntegrationProvider):
    name = "pipedream"

    # ----------------- transport-level wrapping ---------------------------- #

    def _wrap(self, e: pd.PipedreamHTTPError) -> IntegrationError:
        s, body = e.status, e.body
        if s == 401:
            return IntegrationError("auth_failed", status=s, detail=body)
        if s == 403:
            return IntegrationError("permission_denied", status=s, detail=body)
        if s == 404:
            return IntegrationError("not_found", status=s, detail=body)
        if s == 422:
            return IntegrationError("validation", status=s, detail=body)
        if s == 429:
            return IntegrationError("rate_limited", status=s, detail=body)
        return IntegrationError("provider_error", status=s, detail=body)

    async def _call(self, coro):
        try:
            return await coro
        except pd.PipedreamHTTPError as e:
            raise self._wrap(e) from None
        except Exception as e:  # noqa: BLE001
            # aiohttp errors / TimeoutError / decode errors all collapse here.
            raise IntegrationError("network", message=str(e)) from None

    # ----------------- IntegrationProvider impl ---------------------------- #

    async def list_accounts(self, external_user_id: str) -> list[dict]:
        return await self._call(pd.list_accounts(external_user_id))

    async def get_account(self, external_user_id: str, account_id: str) -> dict | None:
        for a in await self.list_accounts(external_user_id):
            if a.get("id") == account_id:
                return a
        return None

    async def validate_connection(
        self, external_user_id: str, account_id: str
    ) -> list[str]:
        try:
            account = await self.get_account(external_user_id, account_id)
        except IntegrationError as e:
            # If we can't even list accounts, don't pre-fail; the action call
            # will surface the real error. Skip validation defensively.
            log.warning("validate_connection_lookup_failed", kind=e.kind, status=e.status)
            return []

        if account is None:
            return ["__not_found__"]

        # OAuth expiry: connector exposes expires_at as ISO 8601.
        exp = account.get("expires_at")
        if exp:
            try:
                dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
                if dt < datetime.now(timezone.utc):
                    return ["__token_expired__"]
            except (ValueError, TypeError):
                pass  # unexpected format; don't block

        # Custom-auth field-level validation. Pipedream's exact account shape
        # varies; check the common keys defensively. We flag values that are
        # explicitly None or empty string; redacted secrets (e.g. "***") count
        # as PRESENT and are not flagged.
        missing: list[str] = []
        bag = None
        for key in ("credentials", "fields", "key"):
            v = account.get(key)
            if isinstance(v, dict):
                bag = v
                break
        if isinstance(bag, dict):
            for k, v in bag.items():
                if v is None or v == "":
                    missing.append(k)
        return missing

    async def list_actions(self, app: str, query: str | None) -> list[dict]:
        return await self._call(pd.search_actions(app, query))

    async def get_action_props(self, action_id: str) -> list[dict]:
        try:
            comp = await pd.get_component(action_id)
        except Exception as e:  # noqa: BLE001
            log.warning("get_action_props_failed", action=action_id, error=str(e))
            return []
        props_in = (comp.get("configurable_props") if isinstance(comp, dict) else None) or []
        out: list[dict] = []
        for p in props_in:
            if not isinstance(p, dict):
                continue
            # Filter auth prop: it's injected by the gateway, never passed by
            # the model. Keeping it would risk the model trying to pass it.
            if p.get("type") == "app":
                continue
            out.append({
                "name": p.get("name"),
                "type": p.get("type"),
                "optional": bool(p.get("optional")),
                "label": p.get("label"),
            })
        return out

    async def _resolve_auth_prop_name(self, action_id: str, app: str) -> str:
        """Find the configurable prop on this action that holds the auth
        reference. Pipedream convention: the prop has `type == "app"` and
        `app == <slug>`; its `name` is what we put `authProvisionId` under
        (often the literal string `"app"`, not the slug). Cached.

        Falls back to the slug if the schema lookup fails -- worst case the
        provider errors and the gateway maps it to an actionable message."""
        cached = _AUTH_PROP_CACHE.get(action_id)
        if cached:
            return cached
        try:
            comp = await pd.get_component(action_id)
        except pd.PipedreamHTTPError as e:
            log.warning("auth_prop_lookup_http_failed", action=action_id, status=e.status)
            return app
        except Exception as e:  # noqa: BLE001
            log.warning("auth_prop_lookup_failed", action=action_id, error=str(e))
            return app
        props = (comp.get("configurable_props") if isinstance(comp, dict) else None) or []
        for p in props:
            if isinstance(p, dict) and p.get("type") == "app" and p.get("app") == app and p.get("name"):
                _AUTH_PROP_CACHE[action_id] = p["name"]
                return p["name"]
        # No matching auth prop found: cache the slug so we don't refetch every
        # call. If this is wrong, the action will fail and the gateway surfaces it.
        _AUTH_PROP_CACHE[action_id] = app
        return app

    async def run_action(
        self,
        external_user_id: str,
        account_id: str,
        app: str,
        action_id: str,
        params: dict,
    ) -> dict:
        # Single shape for OAuth and custom-auth: the connector resolves
        # credentials from the connected account; we never look at them.
        # The KEY under which we put authProvisionId is the action's own auth
        # prop name (often "app", not the slug -- Pipedream convention).
        auth_prop = await self._resolve_auth_prop_name(action_id, app)
        configured = dict(params or {})
        configured[auth_prop] = {"authProvisionId": account_id}
        return await self._call(pd.run_action(external_user_id, action_id, configured))

    async def disconnect(self, account_id: str) -> bool:
        try:
            return await pd.delete_account(account_id)
        except pd.PipedreamHTTPError as e:
            if e.status == 404:
                return False
            raise self._wrap(e) from None
        except Exception as e:  # noqa: BLE001
            raise IntegrationError("network", message=str(e)) from None

    async def create_connect_link(
        self, external_user_id: str, webhook_uri: str | None = None
    ) -> dict:
        return await self._call(pd.create_connect_token(external_user_id, webhook_uri=webhook_uri))

    def match_account_for_app(self, accounts: list[dict], app: str) -> dict | None:
        """Map an upstream account back to the workspace's app slug.

        Pipedream stores accounts under their **canonical** name_slug
        (e.g. `salesforce_rest_api`, `google_sheets`, `hubspot_v3`). The
        user, however, typically asks "conectar salesforce" -- short
        slug. `create_connect_token` already calls `resolve_app_slug`
        so the OAuth flow lands in the right Pipedream app, but the
        resulting account upstream keeps the canonical compound slug.
        Without a matching lookup, the poll fallback (and
        `is_connected`'s reconciler) can't find the account it just
        created.

        Match in three passes, picking the first hit:
          1. Exact `name_slug` / `name` == user slug.
          2. Prefix: `<user_slug>_*` (handles `salesforce` ->
             `salesforce_rest_api`, `google` -> `google_sheets`, etc.).
          3. Token: `<user_slug>` appears as a `_`-separated token
             anywhere in the canonical slug.

        Stays sync so we don't reshape every caller's signature; the
        resolver's network round-trip isn't needed here because we
        already have the account list in memory."""
        needle = (app or "").lower().strip()
        if not needle:
            return None
        # Pass 1: exact (cheap, covers slugs that don't need resolving).
        for a in accounts:
            ao = a.get("app") or {}
            if (ao.get("name_slug") or ao.get("name")) == app:
                return a
        # Pass 2: `<app>_*` -- compound slug starting with the user slug.
        for a in accounts:
            ao = a.get("app") or {}
            ns = (ao.get("name_slug") or "").lower()
            if ns.startswith(needle + "_"):
                return a
        # Pass 3: `<app>` as one of the canonical slug's tokens.
        for a in accounts:
            ao = a.get("app") or {}
            ns = (ao.get("name_slug") or "").lower()
            if needle in ns.split("_"):
                return a
        return None

    def auth_type_of(self, account: dict) -> str | None:
        ao = account.get("app") or {}
        t = (ao.get("auth_type") or account.get("auth_type") or "").lower()
        if t == "oauth":
            return "oauth"
        if t in ("keys", "custom", "custom_auth", "api_key"):
            return "custom"
        return None


# Module-level default. The gateway depends on get_provider(), not the concrete
# class -- when a second provider lands (MCP/HTTP), we'll route here.
_default: IntegrationProvider = PipedreamProvider()


def get_provider() -> IntegrationProvider:
    return _default
