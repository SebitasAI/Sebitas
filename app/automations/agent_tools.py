"""Agent tools for automations.

Six tools, all workspace-scoped via contextvars (same plumbing as the
scheduled-task tools):

  - create_automation
  - list_automations
  - update_automation
  - delete_automation
  - pause_automation
  - resume_automation

Side-effect imports: this module is imported from `app/agent/tools.py`
so the `register(...)` calls run at app boot and the tools appear in
the registry.

The agent UX deliberately mirrors scheduled tasks (kebab-case slug,
confirmation step before mutating) so the user's mental model carries
across both surfaces."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.agent.context import app_user_id_var, workspace_id_var
from app.agent.tools import Tool, register
from app.automations import repository as repo

log = structlog.get_logger(__name__)


def _ctx_workspace_id() -> uuid.UUID | None:
    s = workspace_id_var.get()
    if not s:
        return None
    try:
        return uuid.UUID(s) if isinstance(s, str) else s
    except (ValueError, TypeError):
        return None


def _ctx_user_id() -> uuid.UUID | None:
    s = app_user_id_var.get()
    if not s:
        return None
    try:
        return uuid.UUID(s)
    except (ValueError, TypeError):
        return None


def _format_automation_line(a) -> str:
    status_bits = []
    if a.is_paused:
        status_bits.append("⏸ paused")
    if a.last_fire_status == "failed":
        status_bits.append("❌ last fire failed")
    elif a.last_fire_status == "success":
        status_bits.append("✓ last fire ok")
    status = (" - " + ", ".join(status_bits)) if status_bits else ""
    return (
        f"• `{a.name}` -- on `{a.trigger_type}` -> `{a.action_type}` "
        f"(fired {a.fire_count}x){status}"
    )


# --------------------------------------------------------------------------- #
# create_automation
# --------------------------------------------------------------------------- #


async def _create_automation(
    name: str,
    trigger_type: str,
    action_type: str,
    action_config: dict[str, Any],
    description: str | None = None,
    trigger_filter: dict[str, Any] | None = None,
) -> str:
    workspace_id = _ctx_workspace_id()
    user_id = _ctx_user_id()
    if workspace_id is None or user_id is None:
        return "Error: no hay contexto de workspace/usuario."

    try:
        automation = await repo.create_automation(
            repo.CreateAutomationInput(
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                name=name,
                description=description,
                trigger_type=trigger_type,
                trigger_filter=trigger_filter,
                action_type=action_type,
                action_config=action_config,
            )
        )
    except repo.AutomationValidationError as exc:
        return str(exc)
    except repo.AutomationNameConflict as exc:
        return str(exc)
    except repo.AutomationError as exc:
        return f"No pude crear la automation: {exc}"

    return (
        f"✓ Creé la automation `{automation.name}`: dispara en `{automation.trigger_type}` "
        f"-> `{automation.action_type}`. La podés pausar o borrar cuando quieras."
    )


_TRIGGER_DESCRIPTION = (
    "Tipo de evento que dispara la automation. Válidos:\n"
    "  - `agent_error`: el agente terminó un run con error.\n"
    "  - `tool_failed`: una tool del agente devolvió un error.\n"
    "  - `user_satisfaction_low`: el usuario marcó 👎 en el feedback.\n"
    "  - `scheduled_task_completed`: una scheduled task terminó (ok o no)."
)

_ACTION_DESCRIPTION = (
    "Qué hacer cuando dispara. Válidos:\n"
    "  - `slack_notify`: postear un mensaje en Slack. action_config: "
    "{text: str (template), channel?: str (default DM al creador)}.\n"
    "  - `agent_run`: arrancar un run del agente con un prompt. action_config: "
    "{prompt: str (template), channel?: str (default DM al creador)}."
)

_TEMPLATE_DESCRIPTION = (
    "En el template podés interpolar variables del evento con `{key}` "
    "(ej. `{trace_id}`, `{tool_name}`, `{error}`). Si la key no existe en el "
    "evento, queda como `{key}` literal -- no rompe la automation."
)


_CREATE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": (
                "Slug kebab-case (2-64 chars, letras minúsculas, números, guiones; "
                "sin guion al inicio/fin). Ej: `notify-on-tool-failure`."
            ),
        },
        "description": {
            "type": "string",
            "description": "OPCIONAL. Una frase corta describiendo qué hace.",
        },
        "trigger_type": {
            "type": "string",
            "enum": sorted(repo.VALID_TRIGGER_TYPES),
            "description": _TRIGGER_DESCRIPTION,
        },
        "trigger_filter": {
            "type": "object",
            "description": (
                "OPCIONAL. Filtra qué eventos disparan. Match key-por-key "
                "contra el payload del evento. Ej para `tool_failed`: "
                "{`tool_name`: `run_action`} -- solo dispara si la tool que "
                "falló fue run_action. Pasá un dict vacío {} o omitilo para "
                "matchear cualquier evento del tipo."
            ),
        },
        "action_type": {
            "type": "string",
            "enum": sorted(repo.VALID_ACTION_TYPES),
            "description": _ACTION_DESCRIPTION,
        },
        "action_config": {
            "type": "object",
            "description": (
                "Config de la acción. Para `slack_notify` requiere `text`; "
                "para `agent_run` requiere `prompt`. " + _TEMPLATE_DESCRIPTION
            ),
        },
    },
    "required": ["name", "trigger_type", "action_type", "action_config"],
}


register(
    Tool(
        name="create_automation",
        description=(
            "Create an event-driven automation. ALWAYS confirm with the user "
            "before calling: show the trigger_type, the filter (if any), the "
            "action_type, and the rendered template with example data. Only "
            "call this tool AFTER the user explicitly confirms.\n\n"
            "Use this when the user wants Misterr to REACT to something "
            "happening (a tool failure, a thumbs-down, a scheduled task "
            "completing). For things that should happen on a schedule, use "
            "create_scheduled_task instead."
        ),
        input_schema=_CREATE_INPUT_SCHEMA,
        handler=_create_automation,
    )
)


# --------------------------------------------------------------------------- #
# list_automations
# --------------------------------------------------------------------------- #


async def _list_automations(filter: str = "mine") -> str:
    workspace_id = _ctx_workspace_id()
    user_id = _ctx_user_id()
    if workspace_id is None or user_id is None:
        return "Error: no hay contexto de workspace/usuario."
    if filter not in ("mine", "all"):
        return "Filter inválido. Usá `mine` o `all`."
    automations = await repo.list_automations(
        workspace_id=workspace_id,
        current_user_id=user_id,
        only_mine=(filter == "mine"),
    )
    if not automations:
        if filter == "mine":
            return "No tenés automations creadas. Decime 'creá una automation que...' y la armo."
        return "No hay automations visibles para vos en este workspace."
    header = (
        f"Tenés *{len(automations)}* automations:"
        if filter == "mine"
        else f"Automations visibles para vos ({len(automations)} total):"
    )
    return header + "\n" + "\n".join(_format_automation_line(a) for a in automations)


register(
    Tool(
        name="list_automations",
        description=(
            "List automations. `filter='mine'` (default) muestra solo las "
            "tuyas; `filter='all'` muestra todas las del workspace que ves."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "enum": ["mine", "all"],
                    "description": "Default 'mine'.",
                }
            },
            "required": [],
        },
        handler=_list_automations,
    )
)


# --------------------------------------------------------------------------- #
# update_automation
# --------------------------------------------------------------------------- #


async def _update_automation(
    handle: str,
    description: str | None = None,
    trigger_filter: dict[str, Any] | None = None,
    action_config: dict[str, Any] | None = None,
) -> str:
    workspace_id = _ctx_workspace_id()
    user_id = _ctx_user_id()
    if workspace_id is None or user_id is None:
        return "Error: no hay contexto de workspace/usuario."
    try:
        automation = await repo.update_automation(
            repo.UpdateAutomationInput(
                workspace_id=workspace_id,
                current_user_id=user_id,
                handle=handle,
                description=description,
                trigger_filter=trigger_filter,
                action_config=action_config,
            )
        )
    except repo.AutomationNotFound as exc:
        return str(exc)
    except repo.AutomationPermissionError as exc:
        return str(exc)
    except repo.AutomationValidationError as exc:
        return str(exc)
    except repo.AutomationError as exc:
        return f"No pude actualizar la automation: {exc}"
    return f"✓ Actualicé `{automation.name}`."


register(
    Tool(
        name="update_automation",
        description=(
            "Update fields of an existing automation. trigger_type and "
            "action_type NO se pueden cambiar (borrá y recreá). Pasá solo los "
            "campos a cambiar."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "handle": {
                    "type": "string",
                    "description": "UUID o slug name de la automation.",
                },
                "description": {"type": "string", "description": "Nueva descripción; opcional."},
                "trigger_filter": {
                    "type": "object",
                    "description": "Nuevo filter (reemplaza el anterior). Pasá {} para 'todo'.",
                },
                "action_config": {
                    "type": "object",
                    "description": "Nuevo action_config (reemplaza el anterior).",
                },
            },
            "required": ["handle"],
        },
        handler=_update_automation,
    )
)


# --------------------------------------------------------------------------- #
# delete / pause / resume
# --------------------------------------------------------------------------- #


async def _delete_automation(handle: str) -> str:
    workspace_id = _ctx_workspace_id()
    user_id = _ctx_user_id()
    if workspace_id is None or user_id is None:
        return "Error: no hay contexto de workspace/usuario."
    try:
        a = await repo.delete_automation(
            workspace_id=workspace_id, current_user_id=user_id, handle=handle
        )
    except repo.AutomationNotFound as exc:
        return str(exc)
    except repo.AutomationPermissionError as exc:
        return str(exc)
    except repo.AutomationError as exc:
        return f"No pude borrar la automation: {exc}"
    return f"✓ Borré la automation `{a.name}`. No dispara más."


register(
    Tool(
        name="delete_automation",
        description=(
            "Delete an automation. IRREVERSIBLE -- deja de disparar y se va. "
            "El historial de runs se mantiene (FK SET NULL). Pedí confirmación "
            "antes de llamar."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "handle": {"type": "string", "description": "UUID o slug name."}
            },
            "required": ["handle"],
        },
        handler=_delete_automation,
        risky=True,
    )
)


async def _pause_automation(handle: str) -> str:
    workspace_id = _ctx_workspace_id()
    user_id = _ctx_user_id()
    if workspace_id is None or user_id is None:
        return "Error: no hay contexto de workspace/usuario."
    try:
        a = await repo.pause_automation(
            workspace_id=workspace_id, current_user_id=user_id, handle=handle
        )
    except repo.AutomationNotFound as exc:
        return str(exc)
    except repo.AutomationPermissionError as exc:
        return str(exc)
    except repo.AutomationError as exc:
        return f"No pude pausar la automation: {exc}"
    return f"✓ Pausé `{a.name}`. No dispara hasta que la reanudes."


register(
    Tool(
        name="pause_automation",
        description="Pause an automation. Idempotente: pausar una pausada es no-op.",
        input_schema={
            "type": "object",
            "properties": {
                "handle": {"type": "string", "description": "UUID o slug name."}
            },
            "required": ["handle"],
        },
        handler=_pause_automation,
    )
)


async def _resume_automation(handle: str) -> str:
    workspace_id = _ctx_workspace_id()
    user_id = _ctx_user_id()
    if workspace_id is None or user_id is None:
        return "Error: no hay contexto de workspace/usuario."
    try:
        a = await repo.resume_automation(
            workspace_id=workspace_id, current_user_id=user_id, handle=handle
        )
    except repo.AutomationNotFound as exc:
        return str(exc)
    except repo.AutomationPermissionError as exc:
        return str(exc)
    except repo.AutomationError as exc:
        return f"No pude reanudar la automation: {exc}"
    return f"✓ Reanudé `{a.name}`. Vuelve a disparar."


register(
    Tool(
        name="resume_automation",
        description="Resume a paused automation.",
        input_schema={
            "type": "object",
            "properties": {
                "handle": {"type": "string", "description": "UUID o slug name."}
            },
            "required": ["handle"],
        },
        handler=_resume_automation,
    )
)
