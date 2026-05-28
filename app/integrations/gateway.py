"""Integration gateway: resolves the workspace's connected account and runs
actions through Pipedream with auth injected server-side. The model passes only
app/action/params; the connected-account reference is added here and the
provider credential never enters our process, the model, or the sandbox.

Risk classification is fail-safe: read verbs are safe, write verbs gate, and
anything ambiguous/unknown gates (never default to safe).
"""

from __future__ import annotations

import re
import uuid

import structlog
from langfuse import get_client
from sqlalchemy import select

from app.agent.context import workspace_id_var
from app.db.models import IntegrationConnection
from app.db.session import get_session
from app.integrations import pipedream

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

    Hook: prefer Pipedream metadata when it exposes read/write or side-effects;
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
                    IntegrationConnection.status == "active",
                )
            )
        ).scalar_one_or_none()


async def list_integrations() -> str:
    ws = _current_workspace()
    if not ws:
        return "Error: sin contexto de workspace."
    async with get_session() as session:
        rows = (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.workspace_id == ws,
                    IntegrationConnection.status == "active",
                )
            )
        ).scalars().all()
    if not rows:
        return "No hay integraciones conectadas en este workspace."
    return "Integraciones conectadas:\n" + "\n".join(f"• {r.app}" for r in rows)


async def find_actions(app: str, query: str | None = None) -> str:
    actions = await pipedream.search_actions(app, query)
    if not actions:
        return f"No encontré actions para {app!r}."
    lines = [f"• {a.get('key')} — {a.get('name', '')}" for a in actions[:20]]
    return f"Actions de {app}:\n" + "\n".join(lines)


async def run_action(app: str, action_id: str, params: dict | None = None) -> str:
    ws = _current_workspace()
    if not ws:
        return "Error: sin contexto de workspace."
    conn = await _connection(ws, app)
    if conn is None:
        return f"La integración {app!r} no está conectada en este workspace."

    configured = dict(params or {})
    # Inject the connected-account reference; the provider credential stays at
    # Pipedream and is never exposed to us or the model.
    configured[app] = {"authProvisionId": conn.pipedream_account_id}

    with _langfuse.start_as_current_observation(
        as_type="span", name=f"integration:{app}.{action_id}", input={"app": app, "action": action_id}
    ) as span:
        try:
            result = await pipedream.run_action(str(ws), action_id, configured)
        except Exception as exc:  # noqa: BLE001
            log.warning("integration_action_failed", app=app, action=action_id, error=str(exc))
            span.update(output=f"error: {exc}")
            return f"Error ejecutando {action_id} en {app}: {exc}"
        out = result.get("ret", result) if isinstance(result, dict) else result
        text = str(out)[:3000]
        span.update(output=text[:500])
    log.info("integration_action_done", app=app, action=action_id)
    return text
