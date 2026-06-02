"""CRUD + permission checks for automations.

Mirrors `app/scheduled_tasks/repository.py` deliberately: same error
class names, same slug shape, same resolver semantics (UUID or slug, both
workspace-scoped). The differences:

- No cron / timezone -- automations are event-driven.
- `trigger_filter` is a small JSONB dict we validate shape-only (keys are
  strings, values are primitives).
- `action_config` shape depends on `action_type`; we enforce that
  required keys are present and primitive-typed but don't validate
  Slack channel IDs etc. (the runtime catches those).

Permission model copies the scheduled-task one: local-scope automations
can be edited/deleted only by their owner. Global/system scopes exist
for future use and are gated behind `is_workspace_admin`."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Automation, AutomationRun
from app.db.session import get_session

log = structlog.get_logger(__name__)


# Match scheduled_tasks. Kebab-case slug, 2-64 chars.
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


VALID_TRIGGER_TYPES: frozenset[str] = frozenset(
    {
        "agent_error",
        "tool_failed",
        "user_satisfaction_low",
        "scheduled_task_completed",
    }
)
VALID_ACTION_TYPES: frozenset[str] = frozenset({"slack_notify", "agent_run"})
VALID_SCOPES: frozenset[str] = frozenset({"local", "global", "system"})


# --------------------------------------------------------------------------- #
# Errors (mirror scheduled_tasks)
# --------------------------------------------------------------------------- #


class AutomationError(Exception):
    pass


class AutomationValidationError(AutomationError):
    pass


class AutomationNotFound(AutomationError):
    pass


class AutomationPermissionError(AutomationError):
    pass


class AutomationNameConflict(AutomationError):
    pass


# --------------------------------------------------------------------------- #
# Shape validators
# --------------------------------------------------------------------------- #


def _validate_filter(trigger_filter: dict[str, Any] | None) -> dict[str, Any]:
    """Filters are key/value dicts where keys are strings and values are
    JSON primitives (str, int, float, bool, None). We deliberately
    reject lists / nested dicts in v1 to keep the matching semantics
    obvious. Returns the normalized dict."""
    if trigger_filter is None:
        return {}
    if not isinstance(trigger_filter, dict):
        raise AutomationValidationError(
            "`trigger_filter` debe ser un dict {key: value}."
        )
    for k, v in trigger_filter.items():
        if not isinstance(k, str):
            raise AutomationValidationError(
                f"Las keys de `trigger_filter` deben ser strings; vino {type(k).__name__}."
            )
        if not isinstance(v, (str, int, float, bool, type(None))):
            raise AutomationValidationError(
                f"El valor de `trigger_filter[{k!r}]` debe ser un primitivo "
                f"(str/int/float/bool/null); vino {type(v).__name__}."
            )
    return dict(trigger_filter)


def _validate_action_config(
    action_type: str, action_config: dict[str, Any] | None
) -> dict[str, Any]:
    """Per-action-type schema check. Both action types in v1 require a
    template field (`text` or `prompt`); `channel` is optional."""
    cfg = action_config or {}
    if not isinstance(cfg, dict):
        raise AutomationValidationError("`action_config` debe ser un dict.")

    if action_type == "slack_notify":
        text = cfg.get("text")
        if not isinstance(text, str) or not text.strip():
            raise AutomationValidationError(
                "Para `slack_notify`, `action_config.text` es obligatorio "
                "(template del mensaje a postear)."
            )
    elif action_type == "agent_run":
        prompt = cfg.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise AutomationValidationError(
                "Para `agent_run`, `action_config.prompt` es obligatorio "
                "(template del prompt para el agente)."
            )
    else:
        raise AutomationValidationError(
            f"`action_type` desconocido: `{action_type}`."
        )

    channel = cfg.get("channel")
    if channel is not None and not isinstance(channel, str):
        raise AutomationValidationError(
            "`action_config.channel` debe ser un string (id de canal/DM) o null."
        )
    return dict(cfg)


# --------------------------------------------------------------------------- #
# Resolver
# --------------------------------------------------------------------------- #


async def resolve_automation(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    handle: str,
) -> Automation:
    """Workspace-scoped resolver. Looks up by UUID first (when `handle`
    parses as one), then by slug. Always filters by workspace_id so we
    can't leak across tenants. Raises AutomationNotFound on miss."""
    handle = (handle or "").strip()
    if not handle:
        raise AutomationNotFound("Falta el id o nombre de la automation.")

    if _UUID_RE.match(handle):
        try:
            aid = uuid.UUID(handle)
        except ValueError:
            aid = None
        if aid is not None:
            row = (
                await session.execute(
                    select(Automation).where(
                        Automation.id == aid,
                        Automation.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                return row

    row = (
        await session.execute(
            select(Automation).where(
                Automation.name == handle,
                Automation.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AutomationNotFound(
            f"No encontré ninguna automation llamada `{handle}` en este workspace."
        )
    return row


# --------------------------------------------------------------------------- #
# Permission helpers
# --------------------------------------------------------------------------- #


async def is_workspace_admin(
    session: AsyncSession, *, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> bool:
    """Same stub as scheduled_tasks: returns False until we have a real
    role column. Keeps non-local scopes locked down by default."""
    _ = (session, user_id, workspace_id)
    return False


async def _ensure_can_modify(
    session: AsyncSession,
    automation: Automation,
    *,
    current_user_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> None:
    if automation.scope == "local":
        if automation.owner_user_id != current_user_id:
            raise AutomationPermissionError(
                "No podés editar una automation local de otro usuario. "
                "Pedile al dueño que la modifique."
            )
        return
    if await is_workspace_admin(
        session, user_id=current_user_id, workspace_id=workspace_id
    ):
        return
    raise AutomationPermissionError(
        "Esta automation es de scope `global`/`system`. Solo admins del "
        "workspace pueden modificarla (todavía no hay roles, así que está "
        "bloqueado)."
    )


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


@dataclass
class CreateAutomationInput:
    workspace_id: uuid.UUID
    created_by_user_id: uuid.UUID
    name: str
    description: str | None
    trigger_type: str
    trigger_filter: dict[str, Any] | None
    action_type: str
    action_config: dict[str, Any]


async def create_automation(payload: CreateAutomationInput) -> Automation:
    if not _SLUG_RE.match(payload.name):
        raise AutomationValidationError(
            f"Nombre `{payload.name}` inválido: usá kebab-case (letras minúsculas, "
            "números y guiones; entre 2 y 64 chars, sin guion al inicio/fin)."
        )
    if payload.trigger_type not in VALID_TRIGGER_TYPES:
        raise AutomationValidationError(
            f"`trigger_type` desconocido: `{payload.trigger_type}`. "
            f"Válidos: {', '.join(sorted(VALID_TRIGGER_TYPES))}."
        )
    if payload.action_type not in VALID_ACTION_TYPES:
        raise AutomationValidationError(
            f"`action_type` desconocido: `{payload.action_type}`. "
            f"Válidos: {', '.join(sorted(VALID_ACTION_TYPES))}."
        )
    trig_filter = _validate_filter(payload.trigger_filter)
    action_cfg = _validate_action_config(payload.action_type, payload.action_config)

    async with get_session() as session:
        existing = (
            await session.execute(
                select(Automation.id).where(
                    Automation.workspace_id == payload.workspace_id,
                    Automation.name == payload.name,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise AutomationNameConflict(
                f"Ya existe una automation llamada `{payload.name}` en este "
                "workspace. Pickeá otro nombre."
            )
        automation = Automation(
            workspace_id=payload.workspace_id,
            created_by_user_id=payload.created_by_user_id,
            owner_user_id=payload.created_by_user_id,
            name=payload.name,
            description=payload.description,
            trigger_type=payload.trigger_type,
            trigger_filter=trig_filter,
            action_type=payload.action_type,
            action_config=action_cfg,
            scope="local",
            is_paused=False,
        )
        session.add(automation)
        await session.commit()
        await session.refresh(automation)
    log.info(
        "automation_created",
        automation_id=str(automation.id),
        workspace_id=str(automation.workspace_id),
        trigger_type=automation.trigger_type,
        action_type=automation.action_type,
        name=automation.name,
    )
    return automation


@dataclass
class UpdateAutomationInput:
    workspace_id: uuid.UUID
    current_user_id: uuid.UUID
    handle: str
    description: str | None = None
    trigger_filter: dict[str, Any] | None = None
    action_config: dict[str, Any] | None = None


async def update_automation(payload: UpdateAutomationInput) -> Automation:
    """Partial update. `trigger_type` and `action_type` are NOT updatable
    here -- if the user wants to change those, delete + recreate. That
    keeps the AutomationRun audit trail consistent (each run's
    action_type_snapshot matches the action_type that was live when it
    fired)."""
    async with get_session() as session:
        automation = await resolve_automation(
            session, payload.workspace_id, payload.handle
        )
        await _ensure_can_modify(
            session,
            automation,
            current_user_id=payload.current_user_id,
            workspace_id=payload.workspace_id,
        )

        if payload.description is not None:
            automation.description = payload.description or None
        if payload.trigger_filter is not None:
            automation.trigger_filter = _validate_filter(payload.trigger_filter)
        if payload.action_config is not None:
            automation.action_config = _validate_action_config(
                automation.action_type, payload.action_config
            )

        await session.commit()
        await session.refresh(automation)
    log.info(
        "automation_updated",
        automation_id=str(automation.id),
        workspace_id=str(automation.workspace_id),
    )
    return automation


async def pause_automation(
    *, workspace_id: uuid.UUID, current_user_id: uuid.UUID, handle: str
) -> Automation:
    async with get_session() as session:
        automation = await resolve_automation(session, workspace_id, handle)
        await _ensure_can_modify(
            session,
            automation,
            current_user_id=current_user_id,
            workspace_id=workspace_id,
        )
        automation.is_paused = True
        await session.commit()
        await session.refresh(automation)
    return automation


async def resume_automation(
    *, workspace_id: uuid.UUID, current_user_id: uuid.UUID, handle: str
) -> Automation:
    async with get_session() as session:
        automation = await resolve_automation(session, workspace_id, handle)
        await _ensure_can_modify(
            session,
            automation,
            current_user_id=current_user_id,
            workspace_id=workspace_id,
        )
        automation.is_paused = False
        await session.commit()
        await session.refresh(automation)
    return automation


async def delete_automation(
    *, workspace_id: uuid.UUID, current_user_id: uuid.UUID, handle: str
) -> Automation:
    async with get_session() as session:
        automation = await resolve_automation(session, workspace_id, handle)
        await _ensure_can_modify(
            session,
            automation,
            current_user_id=current_user_id,
            workspace_id=workspace_id,
        )
        await session.delete(automation)
        await session.commit()
    return automation


async def list_automations(
    *,
    workspace_id: uuid.UUID,
    current_user_id: uuid.UUID | None = None,
    only_mine: bool = False,
) -> list[Automation]:
    """List automations visible to the caller. `only_mine` restricts to
    local-scope automations owned by `current_user_id`; otherwise all
    workspace-visible automations come back."""
    async with get_session() as session:
        stmt = select(Automation).where(Automation.workspace_id == workspace_id)
        if only_mine and current_user_id is not None:
            stmt = stmt.where(
                Automation.scope == "local",
                Automation.owner_user_id == current_user_id,
            )
        rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


async def list_runs_for_automation(
    *, workspace_id: uuid.UUID, automation_id: uuid.UUID, limit: int = 20
) -> list[AutomationRun]:
    async with get_session() as session:
        rows = (
            await session.execute(
                select(AutomationRun)
                .where(
                    AutomationRun.workspace_id == workspace_id,
                    AutomationRun.automation_id == automation_id,
                )
                .order_by(AutomationRun.started_at.desc())
                .limit(limit)
            )
        ).scalars().all()
    return list(rows)


__all__ = [
    "AutomationError",
    "AutomationValidationError",
    "AutomationNotFound",
    "AutomationPermissionError",
    "AutomationNameConflict",
    "VALID_TRIGGER_TYPES",
    "VALID_ACTION_TYPES",
    "CreateAutomationInput",
    "UpdateAutomationInput",
    "create_automation",
    "update_automation",
    "pause_automation",
    "resume_automation",
    "delete_automation",
    "list_automations",
    "list_runs_for_automation",
    "resolve_automation",
    "is_workspace_admin",
]
