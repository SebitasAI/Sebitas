"""Hard-coded system tasks seeded into every workspace.

These are NOT configurable from the agent or the web app. The user can pause
them or (in v1) change `destination_slack_id`; everything else is locked at
the tool layer. If we ever ship a new system task, add it here and the
startup seeder picks it up via INSERT ... ON CONFLICT DO NOTHING on the
(workspace_id, name) UNIQUE.

Prompts are in voseo Spanish to match the rest of Misterr's UX. Note: this
hard-codes the language; when we add per-workspace language settings we'll
parameterize from the workspace row. Tracked as a TODO; not blocking T-1.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SystemTaskDefault:
    """Static definition of a system task. Mirrors the columns of
    `scheduled_task` that are immutable once seeded. `destination_type` is
    always 'channel' because system tasks target the workspace's home channel
    (`workspace.bot_home_channel_id`); the seeder fills in the channel id at
    insert time. We deliberately do NOT pin `destination_slack_id` here so
    the seeder can leave it null if the workspace hasn't configured a home
    channel yet (the scheduler then marks the fire failed and lets the next
    tick recover once admin sets it)."""

    name: str
    prompt: str
    cron_spec: str
    timezone: str
    destination_type: str = "channel"


WORKFLOW_DISCOVERY = SystemTaskDefault(
    name="workflow-discovery",
    prompt=(
        "Revisá la actividad del workspace en los últimos 7 días "
        "y detectá tareas repetitivas que podrías automatizar. "
        "Para cada patrón claro, proponé un workflow listo para activar. "
        "Si ya propusiste algo en corridas anteriores (ver last_run_summary "
        "en el contexto de scheduled task), no repitas la misma sugerencia. "
        "Si no encontrás nada útil, no envíes mensaje."
    ),
    # Mon and Wed at 04:01 UTC. Off-hours so it doesn't compete with the
    # daily-brief slot. Workspace can repoint to a non-UTC tz in a future
    # slice; for v1 UTC is fine (the brief at 8:30 UTC is the user-visible one).
    cron_spec="1 4 * * 1,3",
    timezone="UTC",
)


DAILY_BRIEF = SystemTaskDefault(
    name="daily-brief",
    prompt=(
        "Resumí lo que se movió en el workspace en las últimas 24 horas: "
        "mensajes clave, decisiones tomadas, threads abiertos, "
        "y pendientes que requieren atención. "
        "Sé conciso. Si no hay actividad relevante, no envíes mensaje."
    ),
    # Weekdays at 08:30 UTC. v1 ships in UTC; per-workspace tz override is
    # a follow-up slice (when we add language + tz to workspace settings).
    cron_spec="30 8 * * 1-5",
    timezone="UTC",
)


ALL_SYSTEM_TASKS: tuple[SystemTaskDefault, ...] = (
    WORKFLOW_DISCOVERY,
    DAILY_BRIEF,
)


__all__ = [
    "SystemTaskDefault",
    "WORKFLOW_DISCOVERY",
    "DAILY_BRIEF",
    "ALL_SYSTEM_TASKS",
]
