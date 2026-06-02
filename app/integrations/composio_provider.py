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

HTTP-direct fallback (workaround, intentionally narrow): for a specific list
of actions whose Composio wrapper is known broken upstream, run_action
bypasses Composio and hits the app's REST API directly with credentials
read from env (via settings). Remove an action from the bypass list as
soon as Composio fixes the underlying schema. See METABASE_POST_API_CARD.
"""

from __future__ import annotations

import structlog

from app.config import get_settings
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

    async def can_initiate_connection(self, app: str) -> bool:
        """Stricter check than `has_toolkit`. Composio refuses to mint a
        connect link unless an `auth_config` is registered for that
        toolkit in our project (412 'No auth_config found for toolkit X').
        `has_toolkit` only verifies the GLOBAL catalog; this verifies our
        PROJECT can actually use it. Routing must prefer this check when
        deciding whether Composio is the right provider for a new
        connection, otherwise the user gets a connect-link failure.

        Returns True iff the toolkit exists AND at least one auth_config
        is registered for it. Defensive: returns False on any fetch error
        (Composio down, bad key, etc.) so routing falls back to the
        alternative provider rather than hard-failing the user."""
        try:
            if not await cz.toolkit_exists(app):
                return False
            configs = await cz.list_auth_configs(toolkit_slug=app)
            for c in configs:
                if not isinstance(c, dict):
                    continue
                if c.get("id") or c.get("nano_id") or c.get("auth_config_id"):
                    return True
            return False
        except cz.ComposioHTTPError as e:
            log.warning(
                "composio_can_initiate_check_failed",
                app=app, status=e.status,
            )
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "composio_can_initiate_check_errored",
                app=app, error=str(exc)[:200],
            )
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

    # Phrases at the *start* of the error string that signal the user's
    # credentials at the upstream app are bad. We deliberately match only
    # against the leading portion of the message: when Metabase / Pipedream /
    # similar surface a Clojure or Python stack trace for a totally unrelated
    # bug (e.g. constraint violation), substrings like 'authentication' or
    # '401' can appear deep in the trace and previously triggered a false
    # auth_failed. The agent then told the user 'reconectá la cuenta' for
    # what was really a missing-field bug. Bounded-prefix matching makes
    # the classifier far stricter while still catching real auth errors,
    # which providers consistently put at the top of their error string.
    import re as _re
    _AUTH_PREFIX = _re.compile(
        r"^\s*("
        r"unauthenticated"
        r"|unauthorized"
        r"|401\b"
        r"|403\b"
        r"|invalid\s+api[_\s-]*(key|token)"
        r"|invalid\s+credentials?"
        r"|authentication\s+failed"
        r"|api\s+key.{0,40}(invalid|expired|missing|rotated)"
        r"|token\s+expired"
        r"|expired\s+token"
        r"|forbidden\b"
        r")",
        _re.IGNORECASE,
    )

    def _classify_action_error(self, detail: str) -> str:
        """Pick the IntegrationError kind for a body-level Composio error.
        Defaults to provider_error; promotes to auth_failed only when the
        error MESSAGE BEGINS with an auth-related phrase (not just contains
        one buried in a stack trace).

        Why prefix-only: real provider auth errors look like
        'Unauthenticated', '401 Unauthorized', 'Invalid API key', usually as
        the entire message or the first line. False positives (constraint
        violations, validation errors, RPC traces) shove auth-shaped words
        deep into the body and the previous substring match caught those
        by accident. Restricting to the leading chunk eliminates that class
        of misclassification while still catching every real auth case
        observed across Composio + Pipedream in our integration tests.
        """
        if not detail:
            return "provider_error"
        # Only look at the leading portion; auth errors appear at the start.
        head = detail[:200].lstrip().lstrip('"').lstrip("'")
        if self._AUTH_PREFIX.match(head):
            return "auth_failed"
        return "provider_error"

    # Composio actions whose generated schema is broken in a way that makes
    # the call always fail upstream. We bypass Composio for these tenant-by-
    # tenant when fallback credentials are configured. Removing an entry from
    # this set is safe once Composio's wrapper is fixed; the only cost of
    # leaving an obsolete entry is one extra env-var read at action time.
    #
    # METABASE_POST_API_CARD: Composio omits `database_id` at the top level;
    # Metabase's POST /api/card rejects with a NOT NULL constraint violation.
    # Verified by direct curl: identical body succeeds when sent to Metabase
    # ourselves.
    # METABASE_CREATE_DASHBOARD_SAVE_COLLECTION: Composio strips `card_id` from
    # every entry of the `dashcards` array, so dashboards get created with the
    # right grid layout but no actual cards linked (every slot empty).
    # Verified: same payload via direct HTTP creates the dashboard AND links
    # cards correctly. Requires a 2-step flow (POST empty, PUT with dashcards)
    # because Metabase's POST /api/dashboard doesn't accept inline dashcards.
    _COMPOSIO_BROKEN_ACTIONS_WITH_DIRECT_FALLBACK = {
        # Write actions where Composio's wrapper strips required fields:
        "METABASE_POST_API_CARD": ("POST", "/api/card", "metabase"),
        "METABASE_CREATE_DASHBOARD_SAVE_COLLECTION": ("CUSTOM_DASHBOARD", "", "metabase"),
        # Read + execute actions: routed here for workspaces with direct
        # credentials but no Composio connected_account (e.g. Simetrik, where
        # we backfilled the API key into integration_connection rather than
        # going through Composio's OAuth flow). For workspaces WITH a Composio
        # connection, HTTP-direct still works and avoids the connection
        # round-trip; cost is symmetric.
        "METABASE_POST_API_DATASET": ("POST", "/api/dataset", "metabase"),
        "METABASE_GET_API_SEARCH": ("GET", "/api/search", "metabase"),
        "METABASE_GET_API_CARD": ("GET", "/api/card", "metabase"),
        "METABASE_GET_API_COLLECTION": ("GET", "/api/collection", "metabase"),
        "METABASE_GET_API_DATABASE": ("GET", "/api/database", "metabase"),
        "METABASE_GET_API_USER_CURRENT": ("GET", "/api/user/current", "metabase"),
    }

    async def _get_metabase_fallback_creds(self, workspace_id: str) -> tuple[str, str] | None:
        """Return (api_key, base_url) for the per-tenant Metabase fallback,
        reading from the encrypted `direct_credentials_encrypted` column on
        `integration_connection`. Falls back to the legacy env-var path
        (METABASE_FALLBACK_*) when the column is empty, to keep PR #54-era
        single-tenant setups working during the transition. The env path is
        scheduled to go away once the column is the source of truth for all
        live tenants.
        """
        import uuid as _uuid
        from app.integrations.direct_credentials import get_direct_credentials

        # DB-first path (the long-term answer; scales to N tenants without
        # touching env vars).
        try:
            ws_uuid = _uuid.UUID(workspace_id)
        except (ValueError, TypeError):
            ws_uuid = None
        if ws_uuid is not None:
            creds = await get_direct_credentials(ws_uuid, "metabase")
            if isinstance(creds, dict):
                api_key = (creds.get("api_key") or "").strip()
                base_url = (creds.get("base_url") or "").strip()
                if api_key and base_url:
                    return api_key, base_url

        # Env-var fallback (legacy; remove once all live tenants are
        # migrated into the DB column). Only matches when the configured
        # workspace_id equals the caller.
        s = get_settings()
        target_ws = (s.metabase_fallback_workspace_id or "").strip()
        if target_ws and target_ws == workspace_id:
            api_key = (s.metabase_fallback_api_key or "").strip()
            base_url = (s.metabase_fallback_base_url or "").strip()
            if api_key and base_url:
                log.info(
                    "metabase_fallback_via_env_var",
                    workspace_id=workspace_id,
                    msg="DB column empty; using legacy env var. Backfill.",
                )
                return api_key, base_url
        return None

    async def _http_direct_metabase(
        self,
        creds: tuple[str, str],
        method: str,
        path: str,
        body: dict | None,
    ) -> dict:
        """Direct call against the Metabase REST API with the stored API key.
        Method dispatch:
          GET  -> body is sent as query-string params (not JSON body)
          POST / PUT / DELETE -> body is sent as JSON
        Used for the bypass allowlist when direct credentials are configured;
        Composio is still the default for actions not in the allowlist."""
        api_key, base_url = creds
        # base_url often ends in /api; our `path` is /api/...; collapse to avoid /api/api.
        base = base_url.rstrip("/")
        if base.endswith("/api"):
            base = base[:-4]
        import aiohttp
        url = f"{base}{path}"
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=30)
        # GET passes the dict as query-string params; POST/PUT/DELETE pass it as JSON body.
        kwargs: dict = {"headers": headers}
        if method.upper() == "GET":
            if body:
                # aiohttp's `params` doesn't accept list values directly; flatten.
                cleaned = {k: v for k, v in body.items() if v is not None}
                kwargs["params"] = cleaned
        else:
            kwargs["json"] = body
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, url, **kwargs) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    log.warning(
                        "metabase_direct_http_error",
                        method=method, path=path, status=resp.status, body=text[:300],
                    )
                    if resp.status in (401, 403):
                        raise IntegrationError(
                            "auth_failed", status=resp.status, detail=text[:300],
                        )
                    raise IntegrationError(
                        "provider_error", status=resp.status, detail=text[:300],
                    )
                if not text:
                    return {}
                try:
                    import json as _json
                    return _json.loads(text)
                except Exception:  # noqa: BLE001
                    return {"raw": text}

    async def _create_dashboard_two_step(
        self, creds: tuple[str, str], params: dict,
    ) -> dict:
        """Composio's SAVE_COLLECTION action passes dashcards but strips
        card_id from each entry; Metabase's POST /api/dashboard also doesn't
        accept inline dashcards in one shot. Two-step:

          1) POST /api/dashboard with {name, collection_id, ...meta} → empty dashboard
          2) PUT /api/dashboard/:id with {dashcards: [...]} → links cards

        The agent passes `parent_collection_id` (Composio's name); we translate
        to Metabase's `collection_id`. dashcards may arrive with `card_id`
        in each entry (the agent followed our skill instructions); we pass them
        through to step 2 verbatim, only synthesising a temporary `id: -N` per
        entry if missing (Metabase needs an id field on each dashcard for the
        PUT; negative IDs tell it 'new').
        """
        params = dict(params)
        dashcards = params.pop("dashcards", None) or []
        # POST: create empty dashboard.
        create_body = {
            "name": params.get("name"),
            "collection_id": params.get("parent_collection_id") or params.get("collection_id"),
        }
        for k in ("description", "parameters", "tabs", "width", "cache_ttl",
                  "auto_apply_filters", "embedding_params", "enable_embedding"):
            if params.get(k) is not None:
                create_body[k] = params[k]
        if not create_body.get("name"):
            raise IntegrationError(
                "validation", status=None,
                detail="dashboard create: 'name' is required",
            )
        created = await self._http_direct_metabase(creds, "POST", "/api/dashboard", create_body)
        if not isinstance(created, dict) or not created.get("id"):
            raise IntegrationError(
                "provider_error", status=None,
                detail=f"dashboard create returned unexpected shape: {str(created)[:200]}",
            )
        dashboard_id = created["id"]
        if not dashcards:
            return created
        # PUT: attach dashcards. Each entry needs a `card_id` (the linkage)
        # and a placeholder `id` (negative for new ones); the rest passes
        # through (row/col/size_x/size_y/visualization_settings).
        prepared: list[dict] = []
        for i, dc in enumerate(dashcards, start=1):
            if not isinstance(dc, dict):
                continue
            entry = dict(dc)
            if "id" not in entry:
                entry["id"] = -i
            if entry.get("visualization_settings") is None:
                entry["visualization_settings"] = {}
            prepared.append(entry)
        put_body = {"dashcards": prepared}
        updated = await self._http_direct_metabase(
            creds, "PUT", f"/api/dashboard/{dashboard_id}", put_body,
        )
        return updated if isinstance(updated, dict) else created

    def _normalise_metabase_card_body(self, params: dict) -> dict:
        """Ensure POST /api/card's body has every required field at the top
        level. Composio's schema doesn't expose `database_id`, so callers
        sometimes only set it nested under `dataset_query.database`. Metabase
        wants both."""
        body = dict(params)
        if not body.get("database_id"):
            dq = body.get("dataset_query")
            if isinstance(dq, dict) and dq.get("database") is not None:
                body["database_id"] = dq["database"]
        if body.get("visualization_settings") is None:
            body["visualization_settings"] = {}
        return body

    async def run_action(
        self,
        external_user_id: str,
        account_id: str,
        app: str,  # noqa: ARG002 (kept for interface symmetry; Composio infers from tool slug)
        action_id: str,
        params: dict,
    ) -> dict:
        # HTTP-direct bypass: a handful of Composio actions are known broken
        # upstream; if the workspace has fallback creds configured, route
        # this specific action around Composio.
        bypass = self._COMPOSIO_BROKEN_ACTIONS_WITH_DIRECT_FALLBACK.get(action_id)
        if bypass is not None and bypass[2] == "metabase":
            creds = await self._get_metabase_fallback_creds(external_user_id)
            if creds is not None:
                method, path, _ = bypass
                try:
                    if method == "CUSTOM_DASHBOARD":
                        result = await self._create_dashboard_two_step(creds, params)
                    else:
                        # Per-action body normalisation. POST_API_CARD needs
                        # database_id copied from dataset_query.database; the
                        # rest pass params through (GETs become query string
                        # in _http_direct_metabase, POSTs send JSON body).
                        if action_id == "METABASE_POST_API_CARD":
                            body = self._normalise_metabase_card_body(params)
                        else:
                            body = params
                        result = await self._http_direct_metabase(creds, method, path, body)
                    log.info(
                        "metabase_direct_action_ok",
                        action_id=action_id,
                        result_id=result.get("id") if isinstance(result, dict) else None,
                    )
                    return result
                except IntegrationError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "metabase_direct_action_unexpected_failure",
                        action_id=action_id, error=str(exc)[:200],
                    )
                    raise IntegrationError(
                        "provider_error", status=None, detail=str(exc)[:300],
                    )

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
