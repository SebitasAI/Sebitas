"""Integration gateway: resolves the workspace's connected account and runs
actions through an IntegrationProvider with auth injected provider-side.

The model passes only app/action/params; the connected-account reference is
added by the provider and the underlying credential never enters our process,
the model, or the sandbox. This module depends on the IntegrationProvider
interface, not on any specific backend.

Risk classification is fail-safe: read verbs are safe, write verbs gate, and
anything ambiguous/unknown gates (never default to safe)."""

from __future__ import annotations

import re
import uuid

import structlog
from langfuse import get_client
from sqlalchemy import select

from app.agent.context import workspace_id_var
from app.db.models import IntegrationConnection
from app.db.session import get_session
from app.integrations.errors import to_user_message
from app.integrations.pipedream_provider import get_provider
from app.integrations.provider import IntegrationError

log = structlog.get_logger(__name__)
_langfuse = get_client()

_READ_VERBS = {"get", "list", "search", "find", "read", "fetch", "retrieve", "lookup", "describe", "count", "view", "show"}
_WRITE_VERBS = {"create", "update", "delete", "remove", "send", "post", "put", "add", "set", "write", "insert", "upsert", "charge", "pay", "cancel", "archive", "move", "rename", "share", "invite", "modify", "edit", "append", "replace", "trigger", "run", "execute", "drop"}


def _classify(action_id: str) -> bool:
    """Return True if the action should be gated (risky). Fail-safe on ambiguity."""
    tokens = re.split(r"[-_.\s]+", (action_id or "").lower())
    if any(t in _WRITE_VERBS for t in tokens):
        return True
    if any(t in _READ_VERBS for t in tokens):
        return False
    return True  # ambiguous / unknown verb (run_query, execute_sql, ...) -> gate


async def should_gate(action_id: str, metadata: dict | None = None) -> bool:
    """Gate decision for an integration action.

    Hook: prefer provider metadata when it exposes read/write or side-effects;
    per-workspace policy (allow/deny lists, always-approve) would plug in here.
    Not built yet — the verb heuristic with fail-safe default is the workhorse.
    """
    if metadata:
        # e.g. metadata read/write hint would override the heuristic here.
        pass
    return _classify(action_id)


def _current_workspace() -> uuid.UUID | None:
    ws = workspace_id_var.get()
    return uuid.UUID(ws) if ws else None


async def _connection(workspace_id: uuid.UUID, app: str) -> IntegrationConnection | None:
    async with get_session() as session:
        return (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.app == app,
                    IntegrationConnection.status == "connected",
                )
            )
        ).scalar_one_or_none()


async def is_connected(workspace_id: uuid.UUID, app: str) -> bool:
    return await _connection(workspace_id, app) is not None


async def list_integrations() -> str:
    """List the workspace's *connected* integrations with status, auth type
    (oauth | custom, for UX only), and connected-since date.

    Auth type comes from the provider in a single round-trip (one list_accounts
    call) and is informational: it never branches invocation logic."""
    ws = _current_workspace()
    if not ws:
        return "Error: sin contexto de workspace."
    async with get_session() as session:
        rows = (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.workspace_id == ws,
                    IntegrationConnection.status == "connected",
                )
            )
        ).scalars().all()
    if not rows:
        return "No hay integraciones conectadas en este workspace."

    # Best-effort auth-type enrichment. If the provider call fails we still
    # render the local rows -- listing is UX, not a blocker.
    provider = get_provider()
    accounts_by_id: dict[str, dict] = {}
    try:
        for a in await provider.list_accounts(str(ws)):
            aid = a.get("id")
            if aid:
                accounts_by_id[aid] = a
    except IntegrationError as e:
        log.warning("list_integrations_provider_fetch_failed", kind=e.kind, status=e.status)

    lines = []
    for r in rows:
        when = r.created_at.strftime("%Y-%m-%d") if r.created_at else "?"
        acc = accounts_by_id.get(r.pipedream_account_id or "")
        auth = provider.auth_type_of(acc) if acc else None
        suffix = f" · auth: {auth}" if auth else ""
        lines.append(f"• *{r.app}* — {r.status}, conectada desde {when}{suffix}")
    return "Integraciones conectadas:\n" + "\n".join(lines)


async def disconnect_integration(app: str) -> str:
    """Delete the connected account at the provider + mark the row as
    disconnected. Idempotent: not-connected returns a polite no-op; a 404 from
    the provider is treated as already-gone. Tenant-scoped."""
    ws = _current_workspace()
    if not ws:
        return "Error: sin contexto de workspace."
    with _langfuse.start_as_current_observation(
        as_type="span", name=f"integration:disconnect:{app}", input={"app": app}
    ) as span:
        async with get_session() as session:
            row = (
                await session.execute(
                    select(IntegrationConnection).where(
                        IntegrationConnection.workspace_id == ws,
                        IntegrationConnection.app == app,
                        IntegrationConnection.status == "connected",
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                msg = f"*{app}* no está conectada en este workspace."
                span.update(output=msg)
                return msg
            account_id = row.pipedream_account_id
            existed = False
            if account_id:
                try:
                    existed = await get_provider().disconnect(account_id)
                except IntegrationError as e:
                    user_msg = to_user_message(e, app)
                    log.warning("integration_disconnect_failed", app=app, kind=e.kind, status=e.status)
                    span.update(output=f"error: {e.kind}")
                    return user_msg
            row.status = "disconnected"
            row.pending_run_id = None
            row.pending_ctx = None
            await session.commit()
        msg = (
            f"Desconectada *{app}*."
            if existed
            else f"Desconectada *{app}* (ya no existía en el proveedor)."
        )
        span.update(output=msg)
    log.info("integration_disconnected", app=app)
    return msg


async def find_actions(app: str, query: str | None = None) -> str:
    """List actions with their param schemas inline. The schema (top 10, fetched
    in parallel) is what lets the model use the right param names directly --
    without it, the model would guess (e.g. snake_case vs camelCase) and silent
    misses cause 4xx on the action call."""
    import asyncio

    provider = get_provider()
    try:
        actions = await provider.list_actions(app, query)
    except IntegrationError as e:
        log.warning("find_actions_failed", app=app, kind=e.kind, status=e.status)
        return to_user_message(e, app)
    if not actions:
        return f"No encontré actions para {app!r}."

    top = actions[:10]
    props_lists = await asyncio.gather(
        *(provider.get_action_props(a.get("key", "")) for a in top),
        return_exceptions=False,
    )

    def _fmt_prop(p: dict) -> str:
        n, t = p.get("name") or "?", p.get("type") or "?"
        opt = " opt" if p.get("optional") else ""
        return f"`{n}`:{t}{opt}"

    lines: list[str] = []
    for a, props in zip(top, props_lists, strict=False):
        key = a.get("key", "")
        name = a.get("name", "")
        if props:
            params = ", ".join(_fmt_prop(p) for p in props)
            lines.append(f"• `{key}` — {name} · params: {params}")
        else:
            lines.append(f"• `{key}` — {name}")
    # If we truncated, give the model a hint.
    suffix = f"\n\n(mostrando los primeros 10 de {len(actions)})" if len(actions) > 10 else ""
    return f"Actions de {app}:\n" + "\n".join(lines) + suffix


async def run_action_raw(app: str, action_id: str, params: dict | None = None) -> dict:
    """Like run_action but returns the provider's full response dict (no
    stringify, no truncation). For internal callers (Spaces refresh, future
    structured-output paths) that need to parse rows + schema, not display
    them. Raises IntegrationError on auth / validation / provider failures."""
    ws = _current_workspace()
    if not ws:
        raise IntegrationError("network", message="no workspace context")
    conn = await _connection(ws, app)
    if conn is None:
        raise IntegrationError("not_found", detail=f"{app!r} not connected")
    if not conn.pipedream_account_id:
        raise IntegrationError("account_not_found")

    provider = get_provider()
    try:
        missing = await provider.validate_connection(str(ws), conn.pipedream_account_id)
    except IntegrationError:
        missing = []
    if missing:
        if missing == ["__token_expired__"]:
            raise IntegrationError("auth_expired")
        if missing == ["__not_found__"]:
            raise IntegrationError("account_not_found")
        raise IntegrationError("auth_missing_fields", detail=missing)

    return await provider.run_action(
        external_user_id=str(ws),
        account_id=conn.pipedream_account_id,
        app=app,
        action_id=action_id,
        params=params or {},
    )


async def run_action(app: str, action_id: str, params: dict | None = None) -> str:
    """Pre-validate the connection (auth fields present, OAuth not expired),
    then invoke through the provider. Any provider error is mapped to an
    actionable user-facing message."""
    ws = _current_workspace()
    if not ws:
        return "Error: sin contexto de workspace."
    conn = await _connection(ws, app)
    if conn is None:
        return f"La integración {app!r} no está conectada en este workspace."
    if not conn.pipedream_account_id:
        # Connected status but no account id -> shouldn't happen; treat as broken.
        return to_user_message(
            IntegrationError("account_not_found"), app
        )

    provider = get_provider()

    # Pre-invocation validation. Cheap defense-in-depth on top of the provider's
    # own error response; surfaces incomplete connections with the field names
    # before we waste a round-trip on a doomed action.
    try:
        missing = await provider.validate_connection(str(ws), conn.pipedream_account_id)
    except IntegrationError as e:
        log.warning("validate_connection_errored", app=app, kind=e.kind, status=e.status)
        missing = []
    if missing:
        if missing == ["__token_expired__"]:
            return to_user_message(IntegrationError("auth_expired"), app)
        if missing == ["__not_found__"]:
            return to_user_message(IntegrationError("account_not_found"), app)
        return to_user_message(
            IntegrationError("auth_missing_fields", detail=missing), app
        )

    with _langfuse.start_as_current_observation(
        as_type="span", name=f"integration:{app}.{action_id}", input={"app": app, "action": action_id}
    ) as span:
        try:
            result = await provider.run_action(
                external_user_id=str(ws),
                account_id=conn.pipedream_account_id,
                app=app,
                action_id=action_id,
                params=params or {},
            )
        except IntegrationError as e:
            user_msg = to_user_message(e, app)
            log.warning("integration_action_failed", app=app, action=action_id, kind=e.kind, status=e.status)
            span.update(output=f"error: {e.kind}")
            return user_msg

        out = result.get("ret", result) if isinstance(result, dict) else result
        text = str(out)[:3000]
        span.update(output=text[:500])
    log.info("integration_action_done", app=app, action=action_id)
    return text
