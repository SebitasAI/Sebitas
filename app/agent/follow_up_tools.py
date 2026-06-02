"""Agent tool `schedule_follow_up`: agenda a nudge for the current
thread when the agent is waiting on user input.

Side-effect imported from `app/agent/tools.py` so the tool registers
at module load.

When to call it (the agent reads this in the tool description):
  - At end of a turn where you asked for concrete data/decision that
    blocks a downstream task ("necesito el spec para terminar X").
  - NOT for casual asks ("qué color preferís?").
  - NOT when the next user action is obvious from context (e.g. the
    user is mid-OAuth flow -- the integration connect path handles it).

When the worker fires the nudge, the agent reads the user's memory +
the `reason` you wrote and composes a one-line message in the same
thread. If the user replied to the thread before fire time, the
follow-up auto-cancels.
"""

from __future__ import annotations

import uuid

import structlog

from app.agent.context import app_user_id_var, run_id_var, workspace_id_var
from app.agent.tools import Tool, register
from app.follow_ups import repository as repo

log = structlog.get_logger(__name__)


def _ctx_uuid(var) -> uuid.UUID | None:
    raw = var.get()
    if not raw:
        return None
    try:
        return uuid.UUID(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None


async def _schedule_follow_up_handler(
    reason: str,
    wait_hours: int = 24,
) -> str:
    """The tool entrypoint. Looks up channel + conversation_key from
    the agent context vars set by the runner (set_run_context); the
    tool itself doesn't take channel/thread args -- the model
    shouldn't be guessing where it is, the runner already knows."""
    workspace_id = _ctx_uuid(workspace_id_var)
    app_user_id = _ctx_uuid(app_user_id_var)
    run_id = run_id_var.get() or None

    if workspace_id is None or app_user_id is None:
        return "Error: no hay contexto de workspace/usuario."

    # Channel + conversation_key live on a separate context var set by
    # the runner alongside the workspace/user vars. Import locally to
    # avoid a circular at module-load (context module is small but the
    # name lookup is dynamic).
    from app.agent.context import calling_channel_var, calling_conversation_key_var, calling_reply_thread_ts_var

    channel = calling_channel_var.get() or ""
    conversation_key = calling_conversation_key_var.get() or ""
    reply_thread_ts = calling_reply_thread_ts_var.get() or None

    if not channel or not conversation_key:
        return (
            "Error: no pude resolver el thread actual para agendar el "
            "follow-up. Probablemente el runner no setteó el contexto "
            "de la conversación (bug interno)."
        )

    reason = (reason or "").strip()
    if not reason:
        return "El motivo del follow-up no puede estar vacío."
    if len(reason) > 500:
        reason = reason[:500].rstrip() + "…"

    try:
        wait_hours = int(wait_hours)
    except (TypeError, ValueError):
        wait_hours = 24

    if wait_hours < repo.MIN_WAIT_HOURS or wait_hours > repo.MAX_WAIT_HOURS:
        return (
            f"wait_hours debe estar entre {repo.MIN_WAIT_HOURS} y "
            f"{repo.MAX_WAIT_HOURS}."
        )

    new_id = await repo.create_follow_up(
        workspace_id=workspace_id,
        app_user_id=app_user_id,
        channel=channel,
        conversation_key=conversation_key,
        reply_thread_ts=reply_thread_ts,
        reason=reason,
        wait_hours=wait_hours,
        created_by_run_id=run_id,
    )
    if new_id is None:
        return (
            "No pude agendar el follow-up (validación interna falló). "
            "Reintentá con un motivo más corto o un wait_hours distinto."
        )
    return (
        f"✓ Follow-up agendado: te voy a re-escribir en {wait_hours}h sobre "
        f"«{reason[:80]}» si no respondés antes."
    )


register(
    Tool(
        name="schedule_follow_up",
        description=(
            "Agendá un follow-up automático para este thread. Llamá esto "
            "al FINAL de un turno donde le pediste al user algo CONCRETO "
            "que bloquea una task downstream (spec, decisión, datos, "
            "credenciales). Si el user no responde, dentro de "
            "`wait_hours` Misterr le va a re-escribir un ping en el "
            "mismo thread. Si responde antes, el follow-up se auto-"
            "cancela.\n"
            "USÁ para: 'necesito el spec del Q3 para armar el reporte', "
            "'¿aprobás el cambio?', 'pasame las credenciales de Salesforce'.\n"
            "NO USÉS para: preguntas casuales, opinión, charla. NO USÉS "
            "más de UNA vez por turno -- si pediste 3 cosas, agendá UN "
            "follow-up con el motivo agregado.\n"
            "wait_hours default es 24 (mañana). Bajalo si es urgente "
            "(mínimo 1h), subilo si es algo que el user va a tardar "
            "(máximo 168h = una semana)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": (
                        "Frase corta (en español) que describe qué "
                        "estás esperando. Va a aparecer en el ping. "
                        "Ejemplo: 'spec Q3 para armar reporte de "
                        "ingresos'. Máximo 500 chars."
                    ),
                },
                "wait_hours": {
                    "type": "integer",
                    "description": "Horas a esperar. Default 24, min 1, max 168.",
                    "default": 24,
                },
            },
            "required": ["reason"],
        },
        handler=_schedule_follow_up_handler,
    )
)


__all__: list[str] = []
