"""Agent tools for scheduled tasks (slice T-1).

Tools resolve `workspace_id` and `current_user_id` (AppUser.id, the internal
UUID) from contextvars set by the runner. They translate from Slack-friendly
inputs to repository calls, catch domain errors, and render Spanish replies.

`scope` is fixed to 'local' on creation in v1 (the input schema literal blocks
'global' and 'system'). The repository still handles all three for resilience.

Registration: this module is imported for side-effects from
`app/agent/tools.py`. Each `register(Tool(...))` at module load adds an entry
to the global tool registry.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from typing import Any

import structlog

from app.agent.context import app_user_id_var, workspace_id_var
from app.agent.tools import Tool, register
from app.db import repository as db_repo
from app.db.models import ScheduledTask
from app.db.session import get_session
from app.scheduled_tasks import repository as repo
from app.scheduled_tasks.timezone import resolve_timezone

log = structlog.get_logger(__name__)


# --------------------------------------------------------------------------- #
# Context resolution helpers
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


# Match Spanish "natural" inputs like "2026-06-15" or "15/06/2026" for the
# pause `until` arg. Keeping it loose; the parser tries each pattern in order.
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d")


def _parse_until(raw: str | None) -> datetime | None:
    """Parse the `until` arg of pause. Treat the date as midnight UTC on that
    day; the user typically means "until this day", and the scheduler's
    `paused_until <= now()` check makes the exact second irrelevant within
    seconds-level latency."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(raw, fmt).date()
            return datetime.combine(d, time(0, 0, 0, tzinfo=timezone.utc))
        except ValueError:
            continue
    # Last-ditch: full ISO datetime.
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _format_task_line(task: ScheduledTask) -> str:
    """One-line render for the list tool. Truncates the prompt and surfaces
    the most relevant operational state (next run, paused?, last run status)."""
    prompt_preview = (task.prompt or "").strip().replace("\n", " ")
    if len(prompt_preview) > 120:
        prompt_preview = prompt_preview[:120].rstrip() + "…"
    state_bits: list[str] = []
    if task.is_paused:
        if task.paused_until:
            state_bits.append(f"pausada hasta {task.paused_until.date().isoformat()}")
        else:
            state_bits.append("pausada")
    if task.next_run_at and not task.is_paused:
        state_bits.append(f"próxima: {task.next_run_at.isoformat()}")
    if task.last_run_status:
        state_bits.append(f"última: {task.last_run_status}")
    state_str = f" ({'; '.join(state_bits)})" if state_bits else ""
    dest = task.destination_slack_id or "sin destino configurado"
    return (
        f"• `{task.name}` [{task.scope}] -- `{task.cron_spec}` {task.timezone}, "
        f"→ {task.destination_type} {dest}{state_str}\n"
        f"    {prompt_preview}"
    )


# --------------------------------------------------------------------------- #
# create_scheduled_task
# --------------------------------------------------------------------------- #


async def _create_scheduled_task(
    name: str,
    prompt: str,
    cron_spec: str,
    scope: str,
    destination_type: str,
    destination_slack_id: str,
    timezone: str | None = None,
) -> str:
    workspace_id = _ctx_workspace_id()
    user_id = _ctx_user_id()
    if workspace_id is None or user_id is None:
        return "Error: no hay contexto de workspace/usuario."

    if scope != "local":
        # Defense in depth: the JSON schema literal also rejects this.
        return (
            "Por ahora solo puedo crear scheduled tasks de scope `local` (solo "
            "para vos). El scope `global` queda para una versión futura cuando "
            "tengamos el sistema de roles."
        )

    # Resolve tz aliases ("hora Col") and use the caller's Slack profile tz
    # as fallback when the agent didn't pass an explicit timezone. The chain:
    #   1. explicit `timezone` arg -> respected (alias-mapped if needed)
    #   2. otherwise -> slack_user.tz of the calling AppUser
    #   3. otherwise -> UTC (with structlog warning so we can audit drift)
    async with get_session() as session:
        slack_tz = await db_repo.get_slack_tz_for_app_user(session, user_id)
    resolved_tz = resolve_timezone(timezone, fallback_slack_tz=slack_tz)

    try:
        task = await repo.create_task(
            repo.CreateTaskInput(
                workspace_id=workspace_id,
                created_by_user_id=user_id,
                name=name,
                prompt=prompt,
                cron_spec=cron_spec,
                timezone=resolved_tz,
                scope="local",
                destination_type=destination_type,  # type: ignore[arg-type]
                destination_slack_id=destination_slack_id,
            )
        )
    except repo.TaskValidationError as exc:
        return str(exc)
    except repo.TaskNameConflict as exc:
        return str(exc)
    except repo.ScheduledTaskError as exc:
        return f"No pude crear la task: {exc}"

    next_run_msg = (
        f" Próximo disparo: {task.next_run_at.isoformat()} (UTC)."
        if task.next_run_at
        else ""
    )
    return (
        f"✓ Creé la scheduled task `{task.name}`: cron `{task.cron_spec}` en "
        f"`{task.timezone}`, postea en {task.destination_type} "
        f"`{task.destination_slack_id}`.{next_run_msg} "
        "Podés pausarla o borrarla cuando quieras."
    )


# --------------------------------------------------------------------------- #
# list_scheduled_tasks
# --------------------------------------------------------------------------- #


async def _list_scheduled_tasks(filter: str = "mine") -> str:
    workspace_id = _ctx_workspace_id()
    user_id = _ctx_user_id()
    if workspace_id is None or user_id is None:
        return "Error: no hay contexto de workspace/usuario."

    if filter not in ("mine", "all", "system"):
        return "Filter inválido. Usá `mine`, `all` o `system`."

    tasks = await repo.list_tasks(
        workspace_id, user_id, filter_mode=filter  # type: ignore[arg-type]
    )
    if not tasks:
        if filter == "mine":
            return "No tenés scheduled tasks creadas. Decime 'creá una task que...' y la armo."
        if filter == "system":
            return "Este workspace no tiene scheduled tasks del sistema (raro; el seeder debería haberlas creado)."
        return "No hay scheduled tasks visibles para vos en este workspace."

    header = {
        "mine": f"Tenés *{len(tasks)}* scheduled tasks:",
        "all": f"Scheduled tasks visibles para vos ({len(tasks)} total):",
        "system": f"Scheduled tasks del sistema ({len(tasks)}):",
    }[filter]
    return header + "\n" + "\n".join(_format_task_line(t) for t in tasks)


# --------------------------------------------------------------------------- #
# update_scheduled_task
# --------------------------------------------------------------------------- #


async def _update_scheduled_task(
    task_id_or_name: str,
    prompt: str | None = None,
    cron_spec: str | None = None,
    timezone: str | None = None,
    destination_type: str | None = None,
    destination_slack_id: str | None = None,
) -> str:
    workspace_id = _ctx_workspace_id()
    user_id = _ctx_user_id()
    if workspace_id is None or user_id is None:
        return "Error: no hay contexto de workspace/usuario."

    resolved_tz = (
        resolve_timezone(timezone, fallback_slack_tz=None)
        if timezone is not None
        else None
    )

    try:
        task = await repo.update_task(
            repo.UpdateTaskInput(
                workspace_id=workspace_id,
                current_user_id=user_id,
                task_id_or_name=task_id_or_name,
                prompt=prompt,
                cron_spec=cron_spec,
                timezone=resolved_tz,
                destination_type=destination_type,  # type: ignore[arg-type]
                destination_slack_id=destination_slack_id,
            )
        )
    except repo.TaskNotFound as exc:
        return str(exc)
    except repo.TaskPermissionError as exc:
        return str(exc)
    except repo.TaskValidationError as exc:
        return str(exc)
    except repo.ScheduledTaskError as exc:
        return f"No pude actualizar la task: {exc}"

    return (
        f"✓ Actualicé `{task.name}`. Cron actual: `{task.cron_spec}` en `{task.timezone}`. "
        + (
            f"Próximo disparo: {task.next_run_at.isoformat()} (UTC)."
            if task.next_run_at
            else ""
        )
    )


# --------------------------------------------------------------------------- #
# delete_scheduled_task (risky)
# --------------------------------------------------------------------------- #


async def _delete_scheduled_task(task_id_or_name: str) -> str:
    workspace_id = _ctx_workspace_id()
    user_id = _ctx_user_id()
    if workspace_id is None or user_id is None:
        return "Error: no hay contexto de workspace/usuario."
    try:
        deleted_name = await repo.delete_task(
            workspace_id, user_id, task_id_or_name
        )
    except repo.TaskNotFound as exc:
        return str(exc)
    except repo.TaskPermissionError as exc:
        return str(exc)
    except repo.ScheduledTaskError as exc:
        return f"No pude borrar la task: {exc}"
    return f"✓ Borré la scheduled task `{deleted_name}`. No se ejecuta más."


# --------------------------------------------------------------------------- #
# pause_scheduled_task
# --------------------------------------------------------------------------- #


async def _pause_scheduled_task(task_id_or_name: str, until: str | None = None) -> str:
    workspace_id = _ctx_workspace_id()
    user_id = _ctx_user_id()
    if workspace_id is None or user_id is None:
        return "Error: no hay contexto de workspace/usuario."

    until_dt = _parse_until(until)
    if until and until_dt is None:
        return (
            f"No entendí la fecha `{until}`. Usá ISO (`2026-06-15`) o "
            "`DD/MM/YYYY` o dejala vacía para pausa indefinida."
        )

    try:
        task = await repo.pause_task(workspace_id, user_id, task_id_or_name, until=until_dt)
    except repo.TaskNotFound as exc:
        return str(exc)
    except repo.TaskPermissionError as exc:
        return str(exc)
    except repo.ScheduledTaskError as exc:
        return f"No pude pausar la task: {exc}"

    suffix = (
        f" hasta {task.paused_until.date().isoformat()} (UTC); se reanuda sola"
        if task.paused_until
        else " indefinidamente; reanudala con `resume_scheduled_task` cuando quieras"
    )
    return f"✓ Pausé `{task.name}`{suffix}."


# --------------------------------------------------------------------------- #
# resume_scheduled_task
# --------------------------------------------------------------------------- #


async def _resume_scheduled_task(task_id_or_name: str) -> str:
    workspace_id = _ctx_workspace_id()
    user_id = _ctx_user_id()
    if workspace_id is None or user_id is None:
        return "Error: no hay contexto de workspace/usuario."
    try:
        task = await repo.resume_task(workspace_id, user_id, task_id_or_name)
    except repo.TaskNotFound as exc:
        return str(exc)
    except repo.TaskPermissionError as exc:
        return str(exc)
    except repo.ScheduledTaskError as exc:
        return f"No pude reanudar la task: {exc}"
    next_msg = (
        f" Próximo disparo: {task.next_run_at.isoformat()} (UTC)."
        if task.next_run_at
        else ""
    )
    return f"✓ Reanudé `{task.name}`.{next_msg}"


# --------------------------------------------------------------------------- #
# Schemas + registration
# --------------------------------------------------------------------------- #


_CREATE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": (
                "Slug kebab-case único en el workspace (ej: 'daily-revops-report')."
            ),
        },
        "prompt": {
            "type": "string",
            "description": (
                "Lo que Misterr debe hacer cuando la task corra, en lenguaje "
                "natural. Pensalo como el primer mensaje que vos le mandarías "
                "al bot manualmente."
            ),
        },
        "cron_spec": {
            "type": "string",
            "description": (
                "Cron clásico de 5 campos. Ej: '0 9 * * 1-5' (días laborables "
                "a las 9am). NO puede correr más seguido que cada 5 minutos."
            ),
        },
        "timezone": {
            "type": "string",
            "description": (
                "OPCIONAL. Timezone IANA ('America/Bogota') o alias ('hora Col', "
                "'hora Mex'). Si OMITÍS este campo, el sistema usa la timezone "
                "del perfil de Slack del usuario que está hablando. SOLO "
                "pasalo cuando el usuario diga explícitamente una tz distinta "
                "a la suya (ej: 'reporte para Brasil', 'hora Argentina')."
            ),
        },
        "scope": {
            "type": "string",
            "enum": ["local"],
            "description": (
                "En v1 solo soporto 'local' (la task afecta solo al usuario "
                "que la crea). El scope global queda para una slice futura."
            ),
        },
        "destination_type": {
            "type": "string",
            "enum": ["channel", "dm"],
            "description": "'channel' para postear en un canal, 'dm' para DM con el dueño.",
        },
        "destination_slack_id": {
            "type": "string",
            "description": (
                "Channel ID (CXXX...) o user ID (UXXX...) de Slack según el "
                "destination_type. Para DMs del dueño consigo, pasame el U-ID "
                "del usuario actual."
            ),
        },
    },
    "required": [
        "name",
        "prompt",
        "cron_spec",
        "scope",
        "destination_type",
        "destination_slack_id",
    ],
}


register(
    Tool(
        name="create_scheduled_task",
        description=(
            "Create a recurring task that Misterr will execute on a cron "
            "schedule. ALWAYS confirm with the user before calling: show them "
            "the planned name, cron interpretation in plain language, the "
            "timezone you will use, prompt, and destination, then ask "
            "'confirmás?'. Only call this tool AFTER the user explicitly says "
            "yes.\n\n"
            "DO NOT ask the user which timezone to use unless the request is "
            "ambiguous about that (e.g. 'reporte para Brasil', 'a las 9am hora "
            "Argentina'). For the common case ('todos los días a las 9am'), "
            "omit the `timezone` argument -- the system will default to the "
            "user's Slack profile timezone, which is right ~95% of the time. "
            "When you show the preview, just tell the user which tz you're "
            "using so they can correct you if it's wrong (e.g. 'asumiendo "
            "hora Colombia, decime si querés otra').\n\n"
            "v1 only allows scope='local' (the task affects only the calling "
            "user)."
        ),
        input_schema=_CREATE_INPUT_SCHEMA,
        handler=_create_scheduled_task,
    )
)


register(
    Tool(
        name="list_scheduled_tasks",
        description=(
            "List the user's scheduled tasks. `filter='mine'` (default) shows "
            "only the user's own tasks; `filter='system'` shows Misterr's "
            "system tasks (workflow-discovery, daily-brief); `filter='all'` "
            "shows everything visible to this user in the workspace."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "enum": ["mine", "all", "system"],
                    "description": "Which set of tasks to list. Default 'mine'.",
                }
            },
            "required": [],
        },
        handler=_list_scheduled_tasks,
    )
)


_UPDATE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id_or_name": {
            "type": "string",
            "description": "UUID o slug name de la task (ej: 'daily-revops-report').",
        },
        "prompt": {"type": "string", "description": "Nuevo prompt; opcional."},
        "cron_spec": {"type": "string", "description": "Nuevo cron; opcional."},
        "timezone": {"type": "string", "description": "Nuevo timezone (IANA o alias); opcional."},
        "destination_type": {
            "type": "string",
            "enum": ["channel", "dm"],
            "description": "Opcional.",
        },
        "destination_slack_id": {"type": "string", "description": "Nuevo destination Slack id; opcional."},
    },
    "required": ["task_id_or_name"],
}


register(
    Tool(
        name="update_scheduled_task",
        description=(
            "Update fields of an existing scheduled task. Pass only the "
            "fields you want to change; nulls/omissions are kept as-is. "
            "Permission rules: only the owner can edit a local task; system "
            "tasks accept only destination_slack_id changes (any member can "
            "do that in v1)."
        ),
        input_schema=_UPDATE_INPUT_SCHEMA,
        handler=_update_scheduled_task,
    )
)


register(
    Tool(
        name="delete_scheduled_task",
        description=(
            "Delete a scheduled task. IRREVERSIBLE -- the task stops "
            "executing and its history is gone. System tasks cannot be "
            "deleted (only paused). Requires user approval before running."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "task_id_or_name": {
                    "type": "string",
                    "description": "UUID o slug name de la task.",
                }
            },
            "required": ["task_id_or_name"],
        },
        handler=_delete_scheduled_task,
        risky=True,
    )
)


register(
    Tool(
        name="pause_scheduled_task",
        description=(
            "Pause a scheduled task. If `until` is set, the task auto-resumes "
            "on that date. Without `until`, it stays paused until "
            "`resume_scheduled_task`. Idempotent: pausing a paused task is "
            "a no-op semantically."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "task_id_or_name": {"type": "string", "description": "UUID o slug name."},
                "until": {
                    "type": "string",
                    "description": (
                        "ISO date 'YYYY-MM-DD' o 'DD/MM/YYYY' (UTC). Vacío = pausa indefinida."
                    ),
                },
            },
            "required": ["task_id_or_name"],
        },
        handler=_pause_scheduled_task,
    )
)


register(
    Tool(
        name="resume_scheduled_task",
        description="Reanuda una scheduled task pausada. Recomputa next_run_at desde ahora.",
        input_schema={
            "type": "object",
            "properties": {
                "task_id_or_name": {"type": "string", "description": "UUID o slug name."}
            },
            "required": ["task_id_or_name"],
        },
        handler=_resume_scheduled_task,
    )
)


__all__: list[str] = []
