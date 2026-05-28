"""Abstract provider interface for credentialed integrations.

Today the only implementation is PipedreamProvider; tomorrow we'll add MCP /
HTTP / direct-SDK providers as fallbacks for connectors that have limitations.
The gateway, connect flow, and agent depend on this interface, not on
Pipedream directly. When a second provider lands, it plugs in here.

Auth shape is provider-internal: the gateway hands over `(account_id, app,
action_id, params)`; the provider knows how to inject credentials (OAuth token,
custom-auth fields, MCP session, etc.) in its own request format. One code path
above this layer, regardless of OAuth vs custom-auth vs anything else.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class IntegrationError(Exception):
    """Structured error raised by a provider. The gateway maps `kind` (+ optional
    `status`/`detail`) to an actionable user-facing message via `errors.py`.

    Kinds:
      auth_missing_fields    -- conn incomplete; detail = list[str] field names
      auth_expired           -- OAuth token past its expires_at
      auth_failed            -- 401 / unauthenticated at provider call time
      permission_denied      -- 403
      not_found              -- 404 (action or account)
      validation             -- 422 (bad params)
      rate_limited           -- 429
      connector_limitation   -- detected heuristically from response body
      provider_error         -- any other 4xx/5xx
      network                -- timeout / TCP error / unparseable response
    """

    def __init__(
        self,
        kind: str,
        *,
        status: int | None = None,
        detail: str | list[str] | None = None,
        message: str | None = None,
    ) -> None:
        self.kind = kind
        self.status = status
        self.detail = detail
        super().__init__(message or kind)


class IntegrationProvider(ABC):
    """One integration backend. Pipedream today; MCP / HTTP tomorrow.

    All methods are async (the impl owns its own HTTP/transport). Tenancy is
    explicit: external_user_id == workspace_id; a provider must NOT assume a
    global tenant. Implementations should raise `IntegrationError` on failure
    so the gateway can map uniformly to user messages.
    """

    name: str

    @abstractmethod
    async def list_accounts(self, external_user_id: str) -> list[dict]:
        """Connected accounts for the tenant, in the provider's native shape."""

    @abstractmethod
    async def get_account(self, external_user_id: str, account_id: str) -> dict | None:
        """One account by id, or None if it's not present for this tenant."""

    @abstractmethod
    async def validate_connection(
        self, external_user_id: str, account_id: str
    ) -> list[str]:
        """Empty list -> OK. Sentinel `__token_expired__` -> OAuth token expired.
        Sentinel `__not_found__` -> account missing at the provider.
        Else: names of required auth fields that are missing or empty."""

    @abstractmethod
    async def list_actions(self, app: str, query: str | None) -> list[dict]:
        """Available actions for an app (optionally filtered by query)."""

    @abstractmethod
    async def get_action_props(self, action_id: str) -> list[dict]:
        """Per-action configurable props: list of {name, type, optional, label}.
        The auth prop (type='app') is filtered out -- the gateway injects auth
        and the model must not see/pass it. Returns [] if the action has none
        or the lookup fails (gateway falls back to a generic message)."""

    @abstractmethod
    async def run_action(
        self,
        external_user_id: str,
        account_id: str,
        app: str,
        action_id: str,
        params: dict,
    ) -> dict:
        """Invoke an action. The provider injects auth in its own shape; the
        caller never sees credentials. Raises IntegrationError on any failure."""

    @abstractmethod
    async def disconnect(self, account_id: str) -> bool:
        """Delete a connected account at the provider. True if it existed and
        was deleted, False if it was already gone (idempotent)."""

    @abstractmethod
    async def create_connect_link(
        self, external_user_id: str, webhook_uri: str | None = None
    ) -> dict:
        """Mint a short-lived link the user clicks to authorize the connection."""

    @abstractmethod
    def match_account_for_app(self, accounts: list[dict], app: str) -> dict | None:
        """Find a connected account in this provider's list shape for the given
        canonical app slug. Sync: pure inspection of provider-native data."""

    @abstractmethod
    def auth_type_of(self, account: dict) -> str | None:
        """Return 'oauth' | 'custom' | None for a connected account record.
        UX/listing metadata only. NEVER use this to branch invocation logic."""
