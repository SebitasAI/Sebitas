"""Agent tools for persistent workspace memory (slice T-X Phase A).

Single tool in Phase A: `remember(scope, fact)`. The agent calls this when
the user explicitly asks Misterr to remember something durable -- or when
the agent itself decides during the conversation that a fact is worth
keeping (within the conservative bar: durable, not ephemeral).

NO autonomous post-pass extraction in Phase A. That's Phase B (with a
cheap haiku call) so we can measure the cost/value before turning it on
by default.

Side-effect imported from `app/agent/tools.py`, like other tool modules.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select

from app.agent.context import app_user_id_var, workspace_id_var
from app.agent.tools import Tool, register
from app.db.models import AppUser
from app.db.session import get_session
from app.memory import append, seed
from app.memory.constants import COMPANY_SLUG, TEAM_SLUG, user_slug

log = structlog.get_logger(__name__)


def _ctx_workspace_id() -> uuid.UUID | None:
    s = workspace_id_var.get()
    if not s:
        return None
    try:
        return uuid.UUID(s) if isinstance(s, str) else s
    except (ValueError, TypeError):
        return None


def _ctx_app_user_id() -> uuid.UUID | None:
    s = app_user_id_var.get()
    if not s:
        return None
    try:
        return uuid.UUID(s)
    except (ValueError, TypeError):
        return None


async def _calling_slack_user_id(app_user_id: uuid.UUID) -> str | None:
    """Resolve the calling AppUser to its slack_user_id. Used to build
    the per-user slug `users/<id>` when the agent picks scope='user'."""
    async with get_session() as session:
        return (
            await session.execute(
                select(AppUser.slack_user_id).where(AppUser.id == app_user_id)
            )
        ).scalar_one_or_none()


async def _remember_handler(scope: str, fact: str) -> str:
    workspace_id = _ctx_workspace_id()
    app_user_id = _ctx_app_user_id()
    if workspace_id is None or app_user_id is None:
        return "Error: no hay contexto de workspace/usuario."

    fact = (fact or "").strip()
    if not fact:
        return "El fact no puede estar vacío."
    if len(fact) > append.MAX_OBSERVATION_CHARS:
        return (
            f"Fact muy largo (>{append.MAX_OBSERVATION_CHARS} chars). "
            "Resumilo en una frase declarativa."
        )

    if scope == "user":
        slack_user_id = await _calling_slack_user_id(app_user_id)
        if not slack_user_id:
            return "Error: no pude resolver tu Slack id para guardar la memoria."
        skill_name = user_slug(slack_user_id)
        # First-time write for this user: make sure the stub exists.
        # Idempotent if it was already seeded at first-message time.
        await seed.ensure_user_skill(workspace_id, app_user_id, slack_user_id)
    elif scope == "team":
        skill_name = TEAM_SLUG
        await seed.ensure_team_skill(workspace_id)
    elif scope == "company":
        skill_name = COMPANY_SLUG
        await seed.ensure_company_skill(workspace_id)
    else:
        return (
            f"Scope inválido: {scope!r}. Tiene que ser 'user', 'team' o 'company'."
        )

    ok = await append.append_observation(
        workspace_id,
        skill_name,
        text=fact,
        source="explicit-remember",
    )
    if not ok:
        return (
            "No pude guardar la memoria (problema interno; ya quedó en logs). "
            "Reintentá en un momento o decímelo de otra manera."
        )
    label = {"user": "tu memoria personal", "team": "memoria del equipo", "company": "memoria de la empresa"}[scope]
    return f"✓ Anotado en {label}: «{fact}»."


async def _aprende_workspace_handler(channel_id: str | None = None) -> str:
    """Trigger the onboarding scan for the calling workspace.

    Two modes:
      - No `channel_id`: full workspace scan (channels list + members +
        integrations + top-5-channels-by-size deep dive). Same behavior
        as the original Phase D tool.
      - With `channel_id`: deep-scan ONLY that channel (100 messages, up
        to 10 facts). Used when the user asks "aprende del canal #X" or
        when Misterr is freshly invited to a channel and wants context.

    Synchronous from the agent's POV: 5-30s for the full scan, 3-8s for
    the single-channel deep dive.
    """
    workspace_id = _ctx_workspace_id()
    if workspace_id is None:
        return "Error: no hay contexto de workspace."

    # Local import to avoid a circular at module load: onboarding.py
    # depends on app.slack.tokens which transitively touches the agent layer.
    from app.memory.onboarding import run_onboarding_scan, scan_single_channel

    if channel_id:
        try:
            result = await scan_single_channel(workspace_id, channel_id)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "aprende_channel_failed",
                workspace_id=str(workspace_id),
                channel_id=channel_id,
                error=str(exc)[:200],
            )
            return (
                f"Falló el scan del canal {channel_id}. Ya quedó en logs. "
                "Reintentá en un momento."
            )
        if result.get("skipped"):
            reason = result["skipped"]
            if reason == "off_topic":
                return (
                    f"Skipié #{result.get('channel_name')} porque parece un "
                    "canal social/off-topic (no extraigo hechos de ahí)."
                )
            if reason == "history_failed":
                return (
                    f"No pude leer el historial de #{result.get('channel_name') or channel_id}. "
                    "Probablemente no soy miembro o no tengo permiso "
                    "(channels:history)."
                )
            if reason == "no_token":
                return "Error: no hay token de bot para este workspace."
            return f"Scan saltado por: {reason}."
        if not result.get("facts_written"):
            return (
                f"Leí #{result.get('channel_name') or channel_id} "
                f"({result.get('messages_examined', 0)} mensajes) pero no "
                "encontré nada durable que valga la pena guardar."
            )
        return (
            f"✓ Scan de #{result['channel_name']}: agregué "
            f"{result['facts_written']} hechos a memoria de la empresa."
        )

    # No channel_id -> full workspace scan.
    try:
        summary = await run_onboarding_scan(workspace_id)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "aprende_workspace_failed",
            workspace_id=str(workspace_id),
            error=str(exc)[:200],
        )
        return (
            "El scan falló a mitad de camino (ya quedó en logs). Lo que "
            "alcanzamos a leer se guardó. Reintentá en un momento."
        )

    parts: list[str] = []
    if summary.get("channels_written"):
        parts.append(f"{summary['channels_written']} canales")
    if summary.get("members_written"):
        parts.append(f"{summary['members_written']} miembros")
    if summary.get("integrations_written"):
        parts.append(f"integraciones del workspace")
    if summary.get("facts_written"):
        parts.append(
            f"{summary['facts_written']} hechos extraídos de "
            f"{summary.get('channels_scanned', 0)} canales"
        )
    if not parts:
        return (
            "Hice el scan pero no encontré nada nuevo para guardar. "
            "Probablemente el bot todavía no es miembro de ningún canal."
        )
    return "✓ Scan completo. Agregué a memoria: " + ", ".join(parts) + "."


register(
    Tool(
        name="aprende_workspace",
        description=(
            "Trigger a memory-seeding scan. Two modes:\n"
            "  - No arg: full workspace scan. Channels list + member roster "
            "+ integrations + top-5-channels deep dive. Use when the user "
            "says 'aprende del workspace', '/misterr aprende', 'fai un scan'.\n"
            "  - With `channel_id`: deep-scan ONLY that channel (100 msgs, "
            "up to 10 facts). Use when the user asks 'aprende del canal #X', "
            "'qué se ha hablado en #X', 'dame contexto del canal X'. ALSO "
            "use this proactively when the conversation reveals you've just "
            "been invited to a channel and you have no context.\n"
            "Idempotent (Phase C compaction folds duplicates). Returns a "
            "one-line summary."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": (
                        "Optional Slack channel ID (e.g. 'C1234567'). When "
                        "set, deep-scans only that channel. When omitted, "
                        "scans the whole workspace."
                    ),
                },
            },
            "required": [],
        },
        handler=_aprende_workspace_handler,
    )
)


register(
    Tool(
        name="remember",
        description=(
            "Persist a durable fact in the workspace's memory. Use when the "
            "user explicitly says 'recordá que ...', 'guardá que ...', 'que "
            "sepas que ...', 'remember that ...' OR when the conversation "
            "reveals a clearly durable preference / fact that will matter in "
            "future turns. Pick scope deliberately:\n"
            "  - 'user'    facts about the calling user (idioma, herramientas, "
            "estilo, background personal).\n"
            "  - 'team'    facts about other people in the workspace (roles, "
            "quién hace qué, canales y su propósito).\n"
            "  - 'company' facts about the company itself (producto, mercado, "
            "integraciones, stage).\n"
            "Keep `fact` terse (<300 chars). ONE fact per call -- if the user "
            "tells you three things, call remember three times. Skip ephemeral "
            "state ('Sam está cansado hoy'), opinions, jokes -- only durable, "
            "still-true-next-month facts."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["user", "team", "company"],
                    "description": "Memory bucket.",
                },
                "fact": {
                    "type": "string",
                    "description": (
                        "Single durable fact, <300 chars. Terse, declarative."
                    ),
                },
            },
            "required": ["scope", "fact"],
        },
        handler=_remember_handler,
    )
)


__all__: list[str] = []
