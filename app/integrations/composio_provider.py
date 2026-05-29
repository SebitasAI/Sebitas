"""ComposioProvider: second concrete implementation of IntegrationProvider.

Pair of `PipedreamProvider`. Same abstract contract, different backend. The
gateway decides at connect-time which provider to use for a given app
(prefer Composio if their catalogue has the toolkit, else Pipedream).
Once decided, the choice is persisted on the IntegrationConnection row
under `provider` so action invocations don't re-route.

Auth shape: Composio manages OAuth on its hosted auth UI. We never see
credentials; we mint a connect link, the user authorizes, Composio stores
the token, and we invoke tools by referencing the connected_account_id.
That matches what we already do with Pipedream — caller layers are
identical above this file.

Error mapping mirrors PipedreamProvider: HTTP status -> IntegrationError
kind so `errors.to_user_message` produces consistent Spanish messages
regardless of which provider failed.
"""

from __future__ import annotations

import structlog

from app.integrations import composio as cz
from app.integrations.provider import IntegrationError, IntegrationProvider

log = structlog.get_logger(__name__)


class ComposioProvider(IntegrationProvider):
    name = "composio"

    # ----------------- transport-level wrapping ---------------------------- #

    def _wrap(self, e: cz.ComposioHTTPError) -> IntegrationError:
        """Mirror PipedreamProvider's mapping. Composio uses standard HTTP
        codes; the only Composio-specific quirk is that auth misconfigured at
        their layer (no API key set) comes through as `status=0`."""
        s, body = e.status, e.body
        if s == 0:
            # Local config error: COMPOSIO_API_KEY missing. Treat as a
            # provider-side network issue so the gateway returns a friendly
            # message rather than crashing the agent loop.
            return IntegrationError("network", status=s, detail=body)
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
        if 500 <= s < 600:
            return IntegrationError("provider_error", status=s, detail=body)
        return IntegrationError("provider_error", status=s, detail=body)

    # ----------------- catalog discovery ----------------------------------- #

    async def has_toolkit(self, app: str) -> bool:
        """Used by the gateway routing layer at connect-time: 'does Composio
        cover this app?'. Routing prefers Composio when this returns True.
        Defensive: if Composio is down or the API key is bad, return False
        so we fall back to Pipedream rather than hard-failing the user."""
        try:
            return await cz.toolkit_exists(app)
        except cz.ComposioHTTPError as e:
            log.warning("composio_has_toolkit_failed", app=app, status=e.status)
            return False

    # ----------------- IntegrationProvider impl ---------------------------- #

    async def list_accounts(self, external_user_id: str) -> list[dict]:
        try:
            return await cz.list_connections(user_id=external_user_id)
        except cz.ComposioHTTPError as e:
            raise self._wrap(e) from e

    async def get_account(
        self, external_user_id: str, account_id: str
    ) -> dict | None:
        try:
            row = await cz.get_connection(account_id)
        except cz.ComposioHTTPError as e:
            raise self._wrap(e) from e
        if row is None:
            return None
        # Tenant check: Composio scopes by user_id at API level but we double
        # check here so a malformed row can't leak across workspaces.
        owner = row.get("user_id") or row.get("entity_id")
        if owner and owner != external_user_id:
            log.warning(
                "composio_account_tenant_mismatch",
                expected=external_user_id, actual=owner, account_id=account_id,
            )
            return None
        return row

    async def validate_connection(
        self, external_user_id: str, account_id: str
    ) -> list[str]:
        """Composio statuses observed in the wild: ACTIVE, INITIATED (user
        started the auth flow but never finished it), EXPIRED (e.g. didn't
        complete within 10 min), INACTIVE/REVOKED/DELETED, DROPPED (UI name
        for soft-deleted). Only ACTIVE is usable for invocation."""
        row = await self.get_account(external_user_id, account_id)
        if row is None:
            return ["__not_found__"]
        status = (row.get("status") or "").upper()
        if status == "ACTIVE":
            return []
        if status == "EXPIRED":
            return ["__token_expired__"]
        if status == "INITIATED":
            # OAuth handed off to the user's browser but never came back.
            # Surface as not-found so the caller offers a fresh link.
            return ["__not_found__"]
        if status in ("INACTIVE", "REVOKED", "DELETED", "DROPPED"):
            return ["__not_found__"]
        # Unknown status: be defensive and treat as not-found rather than
        # silently passing it through. Better to ask the user to reconnect
        # than to invoke against a half-broken connection.
        log.warning(
            "composio_unknown_connection_status",
            account_id=account_id, status=status,
        )
        return ["__not_found__"]

    async def list_actions(self, app: str, query: str | None) -> list[dict]:
        try:
            tools = await cz.list_tools(app, query)
        except cz.ComposioHTTPError as e:
            raise self._wrap(e) from e
        # Normalise to the shape the gateway / find_actions tool expects
        # (matching pipedream's output): {key, name, description, ...}.
        normalised: list[dict] = []
        for t in tools:
            normalised.append({
                "key": t.get("slug") or t.get("name") or "",
                "name": t.get("display_name") or t.get("name") or t.get("slug") or "",
                "description": t.get("description") or "",
            })
        return normalised

    async def get_action_props(self, action_id: str) -> list[dict]:
        """Composio returns the full tool schema; we pluck the parameters
        (filtering out any auth-shaped fields) so the agent sees only what
        it should pass."""
        try:
            tool = await cz.get_tool(action_id)
        except cz.ComposioHTTPError as e:
            log.warning("composio_get_tool_failed", action_id=action_id, status=e.status)
            return []
        # input_parameters shape varies: sometimes a JSON Schema, sometimes a
        # flat list. Normalise to [{name, type, optional, label}].
        params = tool.get("input_parameters") or tool.get("parameters") or {}
        if isinstance(params, dict) and "properties" in params:
            required = set(params.get("required") or [])
            props: list[dict] = []
            for name, spec in (params.get("properties") or {}).items():
                if not isinstance(spec, dict):
                    continue
                props.append({
                    "name": name,
                    "type": spec.get("type") or "string",
                    "optional": name not in required,
                    "label": spec.get("description") or "",
                })
            return props
        if isinstance(params, list):
            return [
                {
                    "name": p.get("name") or "",
                    "type": p.get("type") or "string",
                    "optional": bool(p.get("optional")),
                    "label": p.get("description") or "",
                }
                for p in params if isinstance(p, dict)
            ]
        return []

    # Substrings in Composio's body-level error that signal the user's
    # credentials at the upstream app are bad, even when Composio's HTTP
    # itself returned 200. We surface these as `auth_failed` so the gateway
    # produces a "reconectá X" message instead of a generic provider error.
    # The agent's previous failure mode was to rationalise the error into
    # something else ("no tengo write access", "tu equipo de RevOps...")
    # because the generic provider_error didn't give it enough signal.
    _AUTH_ERROR_HINTS = (
        "unauthenticated",
        "unauthorized",
        "401",
        "invalid api key",
        "invalid api token",
        "expired token",
        "token expired",
        "invalid credentials",
        "authentication failed",
        "forbidden",
    )

    def _classify_action_error(self, detail: str) -> str:
        """Pick the IntegrationError kind for a body-level Composio error.
        Defaults to provider_error; promotes to auth_failed when the message
        contains anything that points at the stored credential being bad."""
        if not detail:
            return "provider_error"
        d = detail.lower()
        for hint in self._AUTH_ERROR_HINTS:
            if hint in d:
                return "auth_failed"
        return "provider_error"

    async def run_action(
        self,
        external_user_id: str,
        account_id: str,
        app: str,  # noqa: ARG002 (kept for interface symmetry; Composio infers from tool slug)
        action_id: str,
        params: dict,
    ) -> dict:
        try:
            result = await cz.execute_tool(
                tool_slug=action_id,
                user_id=external_user_id,
                arguments=params,
                connected_account_id=account_id or None,
            )
        except cz.ComposioHTTPError as e:
            raise self._wrap(e) from e
        # Composio wraps results as {data, error, successful} typically.
        # Surface error semantics explicitly so the gateway can translate.
        if isinstance(result, dict):
            if result.get("successful") is False or result.get("error"):
                detail = str(result.get("error") or "tool execution failed")
                kind = self._classify_action_error(detail)
                if kind == "auth_failed":
                    log.warning(
                        "composio_action_auth_failed",
                        action_id=action_id, detail=detail[:200],
                    )
                raise IntegrationError(kind, status=None, detail=detail[:300])
            return result.get("data") if "data" in result else result
        return {"ret": result}

    async def disconnect(self, account_id: str) -> bool:
        try:
            return await cz.delete_connection(account_id)
        except cz.ComposioHTTPError as e:
            raise self._wrap(e) from e

    async def create_connect_link(
        self, external_user_id: str, webhook_uri: str | None = None
    ) -> dict:
        """Pipedream's create_connect_link takes only external_user_id (it
        produces a generic link the user binds to one app interactively). The
        Composio API requires the toolkit_slug at link creation time; the
        gateway already knows the app, but our abstract signature doesn't pass
        it here. We document the limitation: callers must use the wider
        Composio-specific connect helper in `connect.py` for production flows.
        This generic implementation returns the user_id only so anything
        depending on the abstract interface still works in tests/lists."""
        return {
            "user_id": external_user_id,
            "_note": (
                "Composio requires toolkit_slug at link creation. Use "
                "composio.initiate_connection(user_id, toolkit_slug) directly."
            ),
            "webhook_uri": webhook_uri,
        }

    # ----------------- pure-data helpers ----------------------------------- #

    def match_account_for_app(
        self, accounts: list[dict], app: str
    ) -> dict | None:
        """Find the most recent ACTIVE connection for this app slug.

        Composio v3 returns the toolkit as a nested object: `acc['toolkit']
        ['slug']`. Older payloads (and our previous code) assumed a flat
        `acc['toolkit_slug']`. We check both. The flat-key check shouldn't ever
        match against the current API but stays in case Composio reverts or a
        downstream caller hands us a normalised payload.

        When multiple connections exist for the same app (laura's reconnect
        loops piled up ACTIVE rows), pick the most recently updated. Filter
        out non-ACTIVE statuses up-front so we never reconcile against
        EXPIRED/INITIATED/DELETED rows that would fail validate_connection
        downstream anyway.
        """
        slug = app.lower()

        def _slug_of(acc: dict) -> str | None:
            # v3 nested shape: {"toolkit": {"slug": "..."}}
            toolkit = acc.get("toolkit")
            if isinstance(toolkit, dict):
                v = toolkit.get("slug")
                if isinstance(v, str):
                    return v.lower()
            # Legacy flat shapes (kept for resilience).
            for key in ("toolkit_slug", "app_unique_key", "app_slug", "app_name"):
                v = acc.get(key)
                if isinstance(v, str):
                    return v.lower()
            return None

        def _is_active(acc: dict) -> bool:
            s = (acc.get("status") or "").upper()
            # `data.status` is sometimes more current than top-level status,
            # check both.
            data = acc.get("data")
            ds = (data.get("status") if isinstance(data, dict) else "") or ""
            return s == "ACTIVE" or ds.upper() == "ACTIVE"

        candidates = [a for a in accounts if _slug_of(a) == slug and _is_active(a)]
        if not candidates:
            return None
        # Pick the most recently updated to avoid reviving a stale row when
        # multiple ACTIVE rows exist (Composio doesn't dedupe on re-auth).
        candidates.sort(
            key=lambda a: a.get("updated_at") or a.get("created_at") or "",
            reverse=True,
        )
        return candidates[0]

    def auth_type_of(self, account: dict) -> str | None:
        """Composio reports auth_scheme: OAUTH2 / API_KEY / BEARER_TOKEN /
        BASIC. We map to the same buckets PipedreamProvider does."""
        scheme = (account.get("auth_scheme") or "").upper()
        if "OAUTH" in scheme:
            return "oauth"
        if scheme:
            return "custom"
        return None


# Singleton convenience accessor (same pattern as get_pipedream_provider).
_PROVIDER_SINGLETON: ComposioProvider | None = None


def get_composio_provider() -> ComposioProvider:
    """Process-scoped singleton. The provider is stateless apart from its
    transport, so one instance is enough."""
    global _PROVIDER_SINGLETON
    if _PROVIDER_SINGLETON is None:
        _PROVIDER_SINGLETON = ComposioProvider()
    return _PROVIDER_SINGLETON
