"""Agent tools for automations.

Seven tools, all workspace-scoped via contextvars (same plumbing as the
scheduled-task tools):

  - create_automation     (branches by source: direct / pipedream / composio)
  - list_automations
  - update_automation
  - delete_automation     (cleans up upstream trigger on pipedream/composio)
  - pause_automation
  - resume_automation
  - rotate_webhook_url    (regenerate the URL secret for source=direct)

Side-effect imports: this module is imported from `app/agent/tools.py`
so the `register(...)` calls run at app boot.

The agent UX deliberately mirrors scheduled tasks: kebab-case slug,
preview + confirmation before mutating, friendly Spanish errors."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.agent.context import app_user_id_var, workspace_id_var
from app.agent.tools import Tool, register
from app.automations import repository as repo
from app.automations import triggers as trig
from app.slack.crypto import TokenCryptoError, decrypt_token

log = structlog.get_logger(__name__)


# --------------------------------------------------------------------------- #
# Context resolution
# --------------------------------------------------------------------------- #


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
        f"• `{a.name}` -- via `{a.source}` (fired {a.fire_count}x){status}"
    )


# --------------------------------------------------------------------------- #
# create_automation
# --------------------------------------------------------------------------- #


_SOURCE_DESCRIPTION = (
    "INTERNAL ENUM (never expose these values verbatim to the user). "
    "De dónde viene el evento. Tres opciones:\n"
    "  - `direct`: Misterr expone una URL única (con secret en el path). "
    "Cualquier sistema externo (script, cron, curl) puede hacer POST "
    "con JSON a esa URL para disparar la automation. Útil para hooks "
    "ad-hoc cuando el evento no viene de un catálogo conocido.\n"
    "  - `pipedream`: usa un trigger del catálogo de triggers nativos "
    "(Langfuse, Linear, GitHub, etc.). Pasa el `pipedream_component_id` "
    "(ej. `langfuse-score-created`) y `pipedream_configured_props` con "
    "los settings del componente.\n"
    "  - `composio`: igual pero usando el catálogo alternativo. Pasa "
    "`composio_trigger_slug` + `composio_config`."
)

_PROMPT_TEMPLATE_DESCRIPTION = (
    "Prompt que recibe el agente cuando dispara la automation. Puedes "
    "interpolar variables del payload con `{key}` o `{nested.key}`. "
    "Ej: para `score:created` el payload tiene `data.trace_id` y "
    "`data.score`, así que `\"Investiga el trace {data.trace_id} que "
    "obtuvo score {data.score}\"` funciona. Si la key no existe, queda "
    "como `{key}` literal: no rompe la automation."
)


async def _create_automation(
    name: str,
    source: str,
    prompt_template: str,
    description: str | None = None,
    destination_channel: str | None = None,
    pipedream_component_id: str | None = None,
    pipedream_configured_props: dict[str, Any] | None = None,
    composio_trigger_slug: str | None = None,
    composio_config: dict[str, Any] | None = None,
    trigger_metadata: dict[str, Any] | None = None,
) -> str:
    workspace_id = _ctx_workspace_id()
    user_id = _ctx_user_id()
    if workspace_id is None or user_id is None:
        return "Error: no hay contexto de workspace/usuario."

    if source not in ("direct", "pipedream", "composio"):
        return (
            f"Source `{source}` no es válido. Los valores admitidos son los "
            "documentados en la spec del tool."
        )

    # For pipedream/composio: provision the upstream trigger FIRST,
    # then create the row with the upstream id + signing key. If
    # provisioning fails, no row is created -- the user retries.
    external_trigger_id: str | None = None
    external_trigger_key: str | None = None
    metadata = dict(trigger_metadata or {})

    # Pre-compute the row id so the webhook URL is stable before the
    # row exists. The DB default is gen_random_uuid() but for the
    # provisioning roundtrip we need to know the id in advance.
    new_id = uuid.uuid4()

    if source == "pipedream":
        if not pipedream_component_id:
            return (
                "Para `source=pipedream` necesito `pipedream_component_id` "
                "(ej. `langfuse-score-created`)."
            )
        try:
            webhook_url = trig.pipedream_webhook_url(str(new_id))
            external_trigger_id, external_trigger_key = (
                await trig.provision_pipedream_trigger(
                    component_id=pipedream_component_id,
                    configured_props=pipedream_configured_props or {},
                    webhook_url=webhook_url,
                    external_user_id=str(workspace_id),
                )
            )
            metadata.setdefault("component_id", pipedream_component_id)
            metadata.setdefault(
                "configured_props", pipedream_configured_props or {}
            )
        except trig.TriggerProvisioningError as exc:
            return f"No pude crear el trigger: {exc}"

    elif source == "composio":
        if not composio_trigger_slug:
            return (
                "Para `source=composio` necesito `composio_trigger_slug`."
            )
        try:
            webhook_url = trig.composio_webhook_url(str(new_id))
            external_trigger_id = await trig.provision_composio_trigger(
                trigger_slug=composio_trigger_slug,
                user_id=str(workspace_id),
                config=composio_config or {},
                webhook_url=webhook_url,
            )
            metadata.setdefault("trigger_slug", composio_trigger_slug)
            metadata.setdefault("config", composio_config or {})
        except trig.TriggerProvisioningError as exc:
            return f"No pude crear el trigger: {exc}"

    try:
        automation = await repo.create_automation(
            repo.CreateAutomationInput(
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                name=name,
                description=description,
                source=source,  # type: ignore[arg-type]
                prompt_template=prompt_template,
                destination_channel=destination_channel,
                trigger_metadata=metadata,
                external_trigger_id=external_trigger_id,
                external_trigger_key_plaintext=external_trigger_key,
            )
        )
    except repo.AutomationValidationError as exc:
        # Best-effort: roll back the upstream trigger if we created one.
        await _cleanup_upstream_on_error(source, external_trigger_id)
        return str(exc)
    except repo.AutomationNameConflict as exc:
        await _cleanup_upstream_on_error(source, external_trigger_id)
        return str(exc)
    except repo.AutomationError as exc:
        await _cleanup_upstream_on_error(source, external_trigger_id)
        return f"No pude crear la automation: {exc}"

    # Surface the user-visible URL on direct creates; for pipedream /
    # composio the URL is internal-only (provider posts to it).
    if automation.source == "direct":
        url = trig.direct_webhook_url(automation.webhook_secret or "")
        return (
            f"✓ Creé `{automation.name}` (source=direct).\n"
            f"Configurá tu sistema externo para POSTear JSON a:\n"
            f"  {url}\n"
            f"Cualquier payload que mandes va a interpolarse en el prompt "
            f"template y disparar el agente. Si la URL se filtra, decime "
            f"`rotame la url de {automation.name}` para regenerar el secret."
        )
    return (
        f"✓ Creé `{automation.name}` (source={automation.source}). "
        f"Trigger upstream id: `{automation.external_trigger_id}`. "
        f"Va a disparar cuando el evento llegue desde "
        f"{automation.source.title()}."
    )


async def _cleanup_upstream_on_error(
    source: str, external_trigger_id: str | None
) -> None:
    if not external_trigger_id:
        return
    try:
        if source == "pipedream":
            await trig.delete_pipedream_trigger(external_trigger_id)
        elif source == "composio":
            await trig.delete_composio_trigger(external_trigger_id)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "automation_create_rollback_failed",
            source=source,
            external_trigger_id=external_trigger_id,
            error=str(exc)[:200],
        )


_CREATE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": (
                "Slug kebab-case (2-64 chars, letras minúsculas, números, guiones; "
                "sin guion al inicio/fin). Ej: `langfuse-low-score-alert`."
            ),
        },
        "source": {
            "type": "string",
            "enum": ["direct", "pipedream", "composio"],
            "description": _SOURCE_DESCRIPTION,
        },
        "prompt_template": {
            "type": "string",
            "description": _PROMPT_TEMPLATE_DESCRIPTION,
        },
        "description": {
            "type": "string",
            "description": "OPCIONAL. Frase corta describiendo qué hace.",
        },
        "destination_channel": {
            "type": "string",
            "description": (
                "OPCIONAL. Slack channel id (C.../G...) o DM id (D...). "
                "Si lo omitís, el agente postea por DM al creador."
            ),
        },
        "pipedream_component_id": {
            "type": "string",
            "description": (
                "Requerido si source=pipedream. ID del componente trigger "
                "en el catálogo de Pipedream (ej. `langfuse-score-created`)."
            ),
        },
        "pipedream_configured_props": {
            "type": "object",
            "description": (
                "OPCIONAL para source=pipedream. Settings específicos del "
                "componente (account ids, filtros). Estructura depende del "
                "componente."
            ),
        },
        "composio_trigger_slug": {
            "type": "string",
            "description": (
                "Requerido si source=composio. Slug del trigger en el "
                "catálogo de Composio (ej. `langfuse_score_created`)."
            ),
        },
        "composio_config": {
            "type": "object",
            "description": (
                "OPCIONAL para source=composio. Config del trigger "
                "(account ids, filtros)."
            ),
        },
        "trigger_metadata": {
            "type": "object",
            "description": (
                "OPCIONAL. Metadata libre para mostrar en la UI (app, "
                "evento). El runtime no la usa."
            ),
        },
    },
    "required": ["name", "source", "prompt_template"],
}


register(
    Tool(
        name="create_automation",
        description=(
            "Create an event-driven automation. ALWAYS confirm with the user "
            "before calling: show the source, the prompt_template, the "
            "destination, and (for pipedream/composio) the upstream trigger "
            "component + config. Only call this tool AFTER the user "
            "explicitly confirms.\n\n"
            "Three sources:\n"
            "  - `direct`: Misterr te da una URL única; el usuario la usa "
            "    desde scripts/crons/sistemas externos para disparar.\n"
            "  - `pipedream`: vos elegís un trigger del catálogo de "
            "    Pipedream (Langfuse, Linear, GitHub, etc.); Pipedream "
            "    invoca a Misterr cuando dispara.\n"
            "  - `composio`: mismo modelo con el catálogo de Composio.\n\n"
            "Para tareas que disparan en un horario (cron), usá "
            "create_scheduled_task en cambio."
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
            return (
                "No tenés automations creadas. Decime 'creá una automation "
                "que...' y la armo."
            )
        return "No hay automations en este workspace todavía."
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
            "List automations. `filter='mine'` (default) muestra las tuyas; "
            "`filter='all'` muestra todas las del workspace."
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
    prompt_template: str | None = None,
    destination_channel: str | None = None,
    clear_destination_channel: bool = False,
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
                prompt_template=prompt_template,
                destination_channel=destination_channel,
                clear_destination_channel=clear_destination_channel,
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
            "Update fields of an existing automation. No se puede cambiar "
            "`source` ni el trigger upstream (borrá y recreá). Pasá solo los "
            "campos a cambiar. `clear_destination_channel=true` resetea el "
            "destino al DM-del-creador default."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "handle": {
                    "type": "string",
                    "description": "UUID o slug name.",
                },
                "description": {"type": "string"},
                "prompt_template": {"type": "string"},
                "destination_channel": {"type": "string"},
                "clear_destination_channel": {"type": "boolean"},
            },
            "required": ["handle"],
        },
        handler=_update_automation,
    )
)


# --------------------------------------------------------------------------- #
# rotate_webhook_url (direct only)
# --------------------------------------------------------------------------- #


async def _rotate_webhook_url(handle: str) -> str:
    workspace_id = _ctx_workspace_id()
    user_id = _ctx_user_id()
    if workspace_id is None or user_id is None:
        return "Error: no hay contexto de workspace/usuario."
    try:
        automation = await repo.rotate_webhook_secret(
            workspace_id=workspace_id, current_user_id=user_id, handle=handle
        )
    except repo.AutomationNotFound as exc:
        return str(exc)
    except repo.AutomationPermissionError as exc:
        return str(exc)
    except repo.AutomationValidationError as exc:
        return str(exc)
    except repo.AutomationError as exc:
        return f"No pude rotar el secret: {exc}"
    url = trig.direct_webhook_url(automation.webhook_secret or "")
    return (
        f"✓ Roté el secret de `{automation.name}`. Nueva URL:\n  {url}\n"
        f"La URL anterior dejó de funcionar. Actualizá tu sistema externo."
    )


register(
    Tool(
        name="rotate_webhook_url",
        description=(
            "Regenera el secret en la URL de una automation source=direct. "
            "Usalo si la URL se filtró o querés rotar proactivamente. La "
            "URL anterior deja de funcionar inmediatamente."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "handle": {"type": "string", "description": "UUID o slug name."}
            },
            "required": ["handle"],
        },
        handler=_rotate_webhook_url,
        risky=True,
    )
)


# --------------------------------------------------------------------------- #
# pause / resume / delete
# --------------------------------------------------------------------------- #


async def _delete_automation(handle: str) -> str:
    workspace_id = _ctx_workspace_id()
    user_id = _ctx_user_id()
    if workspace_id is None or user_id is None:
        return "Error: no hay contexto de workspace/usuario."
    try:
        snapshot = await repo.delete_automation(
            workspace_id=workspace_id, current_user_id=user_id, handle=handle
        )
    except repo.AutomationNotFound as exc:
        return str(exc)
    except repo.AutomationPermissionError as exc:
        return str(exc)
    except repo.AutomationError as exc:
        return f"No pude borrar la automation: {exc}"

    # Best-effort: clean up the upstream trigger. Our row is already
    # gone, so failure here is operational (orphaned upstream trigger
    # POSTs to a URL that 404s).
    if snapshot["source"] == "pipedream" and snapshot["external_trigger_id"]:
        await trig.delete_pipedream_trigger(snapshot["external_trigger_id"])
    elif snapshot["source"] == "composio" and snapshot["external_trigger_id"]:
        await trig.delete_composio_trigger(snapshot["external_trigger_id"])

    return f"✓ Borré la automation `{snapshot['name']}`. No dispara más."


register(
    Tool(
        name="delete_automation",
        description=(
            "Delete an automation. IRREVERSIBLE -- deja de disparar y se va. "
            "Si la source es pipedream/composio, también borra el trigger "
            "upstream. El historial de runs se mantiene (FK SET NULL). "
            "Pedí confirmación antes de llamar."
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
        description="Pause an automation. Idempotente.",
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
    return f"✓ Reanudé `{a.name}`."


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
