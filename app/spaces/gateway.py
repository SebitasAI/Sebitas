"""Spaces gateway: resolves the workspace context and orchestrates lifecycle
via a SpaceBackend. The model passes name + data_binding + access_list;
provisioning refs / URLs come from the backend and get persisted on `space`.

Tenancy: every op scopes to `workspace_id_var` from the contextvar set by
the runner. A workspace can ONLY manage its own Spaces; cross-tenant access
is impossible by query construction.
"""

from __future__ import annotations

import uuid

import structlog
from langfuse import get_client
from sqlalchemy import select

from app.agent.context import workspace_id_var
from app.db.models import Space
from app.db.session import get_session
from app.spaces.backend import SpaceBackend
from app.spaces.mock import MockSpaceBackend

log = structlog.get_logger(__name__)
_langfuse = get_client()

# Module-level default backend. 4B-ii swaps in ConvexSharedSpaceBackend via
# `set_backend` from the lifespan, without touching this gateway.
_backend: SpaceBackend = MockSpaceBackend()


def get_backend() -> SpaceBackend:
    return _backend


def set_backend(b: SpaceBackend) -> None:
    """For tests / runtime swap (4B-ii will use this in the FastAPI lifespan)."""
    global _backend
    _backend = b


def _current_workspace() -> uuid.UUID | None:
    ws = workspace_id_var.get()
    return uuid.UUID(ws) if ws else None


async def _get_space(space_id_str: str) -> Space | None:
    """Tenant-scoped fetch: returns the Space ONLY if it belongs to the
    workspace in context. Cross-tenant access returns None (same as not found)."""
    ws = _current_workspace()
    if not ws:
        return None
    try:
        sid = uuid.UUID(space_id_str)
    except (ValueError, TypeError):
        return None
    async with get_session() as session:
        return (
            await session.execute(
                select(Space).where(Space.id == sid, Space.workspace_id == ws)
            )
        ).scalar_one_or_none()


# --------------------------------------------------------------------------- #
# Tool-level operations (called from agent/tools.py via _spaces.<name>)
# --------------------------------------------------------------------------- #


async def deploy_space(name: str, data_binding: dict, access_list: list) -> str:
    ws = _current_workspace()
    if not ws:
        return "Error: sin contexto de workspace."
    if not isinstance(name, str) or not name.strip():
        return "Error: el Space necesita un nombre."
    if not isinstance(data_binding, dict) or not data_binding:
        return "Error: `data_binding` requerido (al menos `app` y `action_id`)."
    if not isinstance(access_list, list):
        return "Error: `access_list` debe ser una lista."
    # Minimal shape validation -- we don't validate the action exists yet
    # (find_actions / run_action would). Tilt towards letting the binding
    # through; the refresh action will surface real errors.
    if not (data_binding.get("app") and data_binding.get("action_id")):
        return "Error: `data_binding` debe incluir `app` y `action_id`."

    backend = get_backend()
    space_id = uuid.uuid4()
    with _langfuse.start_as_current_observation(
        as_type="span", name=f"space:deploy:{name}",
        input={"name": name, "app": data_binding.get("app"), "n_access": len(access_list)},
    ) as span:
        try:
            deployment = await backend.deploy(
                space_id=space_id, workspace_id=ws, name=name.strip(),
                data_binding=data_binding, access_list=access_list,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("space_deploy_failed", error=str(exc))
            span.update(output=f"error: {exc}")
            return f"Error desplegando el Space: {exc}"
        async with get_session() as session:
            session.add(Space(
                id=space_id, workspace_id=ws, name=name.strip(),
                convex_project_ref=deployment.convex_project_ref,
                convex_deployment_ref=deployment.convex_deployment_ref,
                frontend_url=deployment.frontend_url,
                admin_key_vault_ref=deployment.admin_key_vault_ref,
                data_binding=data_binding, access_list=access_list,
                status="deployed",
            ))
            await session.commit()
        span.update(output=f"deployed {space_id}")

    log.info("space_deployed", space_id=str(space_id), name=name)
    url = deployment.frontend_url or "(sin URL todavía)"
    return (
        f"Space *{name}* desplegado.\n"
        f"• URL: <{url}|abrir>\n"
        f"• id: `{space_id}`\n"
        f"• {len(access_list)} usuario(s) con acceso"
    )


async def list_spaces() -> str:
    ws = _current_workspace()
    if not ws:
        return "Error: sin contexto de workspace."
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Space)
                .where(Space.workspace_id == ws)
                .order_by(Space.created_at)
            )
        ).scalars().all()
    if not rows:
        return "No hay Spaces en este workspace todavía. Pedime «deploy un space» para empezar."
    lines = []
    for r in rows:
        n_access = len(r.access_list) if isinstance(r.access_list, list) else 0
        when = r.created_at.strftime("%Y-%m-%d") if r.created_at else "?"
        if r.frontend_url:
            url_str = f"<{r.frontend_url}|abrir>"
        else:
            url_str = "(sin URL)"
        lines.append(
            f"• *{r.name}* [{r.status}] — {url_str} · {n_access} con acceso · "
            f"creado {when} · id `{r.id}`"
        )
    return "Spaces del workspace:\n" + "\n".join(lines)


async def delete_space(space_id: str) -> str:
    space = await _get_space(space_id)
    if space is None:
        return f"No encontré el Space `{space_id}` en este workspace."
    name = space.name
    backend = get_backend()
    with _langfuse.start_as_current_observation(
        as_type="span", name=f"space:delete:{name}", input={"space_id": str(space.id)},
    ) as span:
        try:
            await backend.delete(space_id=space.id)
        except Exception as exc:  # noqa: BLE001
            log.warning("space_delete_failed", error=str(exc), space_id=str(space.id))
            span.update(output=f"error: {exc}")
            return f"Error al borrar el Space *{name}*: {exc}"
        async with get_session() as session:
            await session.execute(Space.__table__.delete().where(Space.id == space.id))
            await session.commit()
        span.update(output="deleted")
    log.info("space_deleted", space_id=str(space.id), name=name)
    return f"Space *{name}* eliminado."


async def update_space_access(space_id: str, access_list: list) -> str:
    space = await _get_space(space_id)
    if space is None:
        return f"No encontré el Space `{space_id}` en este workspace."
    if not isinstance(access_list, list):
        return "Error: `access_list` debe ser una lista."
    backend = get_backend()
    with _langfuse.start_as_current_observation(
        as_type="span", name=f"space:update_access:{space.name}",
        input={"space_id": str(space.id), "n_access": len(access_list)},
    ) as span:
        try:
            await backend.update_access(space_id=space.id, access_list=access_list)
        except Exception as exc:  # noqa: BLE001
            log.warning("space_update_access_failed", error=str(exc))
            span.update(output=f"error: {exc}")
            return f"Error al actualizar acceso del Space *{space.name}*: {exc}"
        async with get_session() as session:
            row = await session.get(Space, space.id)
            if row is not None:
                row.access_list = access_list
                await session.commit()
        span.update(output=f"updated to {len(access_list)} entries")
    return f"Acceso del Space *{space.name}* actualizado: {len(access_list)} usuario(s)."


async def update_space_binding(space_id: str, data_binding: dict) -> str:
    space = await _get_space(space_id)
    if space is None:
        return f"No encontré el Space `{space_id}` en este workspace."
    if not isinstance(data_binding, dict) or not data_binding:
        return "Error: `data_binding` requerido."
    if not (data_binding.get("app") and data_binding.get("action_id")):
        return "Error: `data_binding` debe incluir `app` y `action_id`."
    backend = get_backend()
    with _langfuse.start_as_current_observation(
        as_type="span", name=f"space:update_binding:{space.name}",
        input={"space_id": str(space.id), "app": data_binding.get("app")},
    ) as span:
        try:
            await backend.update_binding(space_id=space.id, data_binding=data_binding)
        except Exception as exc:  # noqa: BLE001
            log.warning("space_update_binding_failed", error=str(exc))
            span.update(output=f"error: {exc}")
            return f"Error al actualizar binding del Space *{space.name}*: {exc}"
        async with get_session() as session:
            row = await session.get(Space, space.id)
            if row is not None:
                row.data_binding = data_binding
                await session.commit()
        span.update(output="updated")
    return f"Data binding del Space *{space.name}* actualizado."
