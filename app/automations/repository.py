"""CRUD + permission checks for automations.

Mirrors `app/scheduled_tasks/repository.py`: same error class names, same
slug shape, same workspace-scoped resolver.

The repository is source-agnostic. Source-specific provisioning (calling
the Pipedream / Composio APIs to create the upstream trigger) lives in
`app/automations/triggers.py` and runs from the agent tool before
calling `create_automation` -- by the time we hit this module, the
provisioning side effects are done and we just persist the row.

That separation keeps this file pure-DB (easy to unit-test) and keeps
the provisioning code in one place where the HTTP retries and the
encryption-of-the-returned-key happen together.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Automation, AutomationRun
from app.db.session import get_session
from app.slack.crypto import encrypt_token

log = structlog.get_logger(__name__)


# Match scheduled_tasks. Kebab-case slug, 2-64 chars.
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


VALID_SOURCES: frozenset[str] = frozenset({"direct", "pipedream", "composio"})
VALID_SCOPES: frozenset[str] = frozenset({"local", "global", "system"})


def generate_webhook_secret() -> str:
    """32 bytes of randomness, base64url-encoded. The URL secret is the
    credential for source=direct -- treat it like a password (rotate on
    suspected leak, never log)."""
    return secrets.token_urlsafe(32)


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
# Resolver
# --------------------------------------------------------------------------- #


async def resolve_automation(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    handle: str,
) -> Automation:
    """Workspace-scoped resolver. UUID first, then slug. Always filters
    by workspace_id so we can't leak across tenants."""
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


async def resolve_by_webhook_secret(
    session: AsyncSession, secret: str
) -> Automation | None:
    """Used by the direct-webhook endpoint. Looks up by global secret
    (unique across workspaces, see uq_automation_webhook_secret).
    Returns None on miss so the endpoint can 404 without distinguishing
    "wrong secret" from "valid secret on a paused automation"."""
    if not secret:
        return None
    return (
        await session.execute(
            select(Automation).where(Automation.webhook_secret == secret)
        )
    ).scalar_one_or_none()


# --------------------------------------------------------------------------- #
# Permission helpers
# --------------------------------------------------------------------------- #


async def is_workspace_admin(
    session: AsyncSession, *, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> bool:
    """Same stub as scheduled_tasks. Returns False until we have a real
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
    source: Literal["direct", "pipedream", "composio"]
    prompt_template: str
    destination_channel: str | None
    trigger_metadata: dict[str, Any]
    # source=direct: leave None; we generate the secret ourselves so the
    # caller never has to think about cryptographic randomness.
    # source=pipedream/composio: the provisioner (triggers.py) sets this
    # with the upstream-returned id.
    external_trigger_id: str | None = None
    # source=pipedream: signing key returned by Pipedream once. We
    # encrypt at-rest with the same Fernet key as bot_token.
    # source=composio: None (account-wide secret in Doppler).
    # source=direct: None.
    external_trigger_key_plaintext: str | None = None


async def create_automation(payload: CreateAutomationInput) -> Automation:
    if not _SLUG_RE.match(payload.name):
        raise AutomationValidationError(
            f"Nombre `{payload.name}` inválido: usá kebab-case (letras minúsculas, "
            "números y guiones; entre 2 y 64 chars, sin guion al inicio/fin)."
        )
    if payload.source not in VALID_SOURCES:
        raise AutomationValidationError(
            f"`source` desconocido: `{payload.source}`. "
            f"Válidos: {', '.join(sorted(VALID_SOURCES))}."
        )
    if not (payload.prompt_template or "").strip():
        raise AutomationValidationError(
            "`prompt_template` es obligatorio (el prompt que va a recibir el "
            "agente; podés interpolar variables del payload con `{key}`)."
        )

    # Source-specific column validation. The DB also enforces this via
    # the check constraint, but we want a friendly error before hitting
    # the DB.
    webhook_secret: str | None = None
    external_trigger_id = payload.external_trigger_id
    external_trigger_key_encrypted: str | None = None

    if payload.source == "direct":
        if payload.external_trigger_id is not None:
            raise AutomationValidationError(
                "Para `source=direct`, `external_trigger_id` debe ser None."
            )
        if payload.external_trigger_key_plaintext is not None:
            raise AutomationValidationError(
                "Para `source=direct`, `external_trigger_key_plaintext` debe ser None."
            )
        webhook_secret = generate_webhook_secret()
    elif payload.source == "pipedream":
        if not payload.external_trigger_id:
            raise AutomationValidationError(
                "Para `source=pipedream`, `external_trigger_id` es obligatorio "
                "(lo devuelve Pipedream al crear el trigger)."
            )
        if not payload.external_trigger_key_plaintext:
            raise AutomationValidationError(
                "Para `source=pipedream`, `external_trigger_key_plaintext` es "
                "obligatorio (la signing key per-trigger)."
            )
        external_trigger_key_encrypted = encrypt_token(
            payload.external_trigger_key_plaintext
        )
    elif payload.source == "composio":
        if not payload.external_trigger_id:
            raise AutomationValidationError(
                "Para `source=composio`, `external_trigger_id` es obligatorio."
            )
        if payload.external_trigger_key_plaintext is not None:
            raise AutomationValidationError(
                "Para `source=composio`, `external_trigger_key_plaintext` debe "
                "ser None (Composio usa el secret de Doppler)."
            )

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
            source=payload.source,
            prompt_template=payload.prompt_template,
            destination_channel=payload.destination_channel,
            webhook_secret=webhook_secret,
            external_trigger_id=external_trigger_id,
            external_trigger_key_encrypted=external_trigger_key_encrypted,
            trigger_metadata=dict(payload.trigger_metadata or {}),
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
        source=automation.source,
        name=automation.name,
    )
    return automation


@dataclass
class UpdateAutomationInput:
    workspace_id: uuid.UUID
    current_user_id: uuid.UUID
    handle: str
    description: str | None = None
    prompt_template: str | None = None
    destination_channel: str | None = None
    # `destination_channel=None` is ambiguous: keep-current vs clear. We
    # use a sentinel for "clear back to default DM".
    clear_destination_channel: bool = False
    trigger_metadata: dict[str, Any] | None = None


async def update_automation(payload: UpdateAutomationInput) -> Automation:
    """Partial update. `source`, `external_trigger_*`, and `webhook_secret`
    cannot be changed here -- those tie to upstream state. If the user
    wants to change source they delete + recreate (also cleans up the
    upstream trigger via the delete path)."""
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
        if payload.prompt_template is not None:
            if not payload.prompt_template.strip():
                raise AutomationValidationError(
                    "`prompt_template` no puede ser vacío."
                )
            automation.prompt_template = payload.prompt_template
        if payload.clear_destination_channel:
            automation.destination_channel = None
        elif payload.destination_channel is not None:
            automation.destination_channel = payload.destination_channel
        if payload.trigger_metadata is not None:
            automation.trigger_metadata = dict(payload.trigger_metadata)

        await session.commit()
        await session.refresh(automation)
    log.info(
        "automation_updated",
        automation_id=str(automation.id),
        workspace_id=str(automation.workspace_id),
    )
    return automation


async def rotate_webhook_secret(
    *, workspace_id: uuid.UUID, current_user_id: uuid.UUID, handle: str
) -> Automation:
    """Generate a new webhook_secret (only valid for source=direct).
    The old secret stops working immediately. Use when the URL has
    been leaked or the user wants to rotate proactively."""
    async with get_session() as session:
        automation = await resolve_automation(session, workspace_id, handle)
        await _ensure_can_modify(
            session,
            automation,
            current_user_id=current_user_id,
            workspace_id=workspace_id,
        )
        if automation.source != "direct":
            raise AutomationValidationError(
                "Solo las automations de `source=direct` tienen webhook_secret "
                "rotable."
            )
        automation.webhook_secret = generate_webhook_secret()
        await session.commit()
        await session.refresh(automation)
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
) -> dict[str, Any]:
    """Delete the row + return its key fields so the caller can clean
    up the upstream trigger (Pipedream / Composio) using
    `external_trigger_id`. Deleting our row first is intentional: even
    if upstream cleanup fails, our side is consistent (no future events
    match this row's webhook URL because the row is gone).

    Returns a dict (not the ORM object) because the row is gone by the
    time we return -- SQLAlchemy would error on attribute access."""
    async with get_session() as session:
        automation = await resolve_automation(session, workspace_id, handle)
        await _ensure_can_modify(
            session,
            automation,
            current_user_id=current_user_id,
            workspace_id=workspace_id,
        )
        snapshot = {
            "id": automation.id,
            "name": automation.name,
            "source": automation.source,
            "external_trigger_id": automation.external_trigger_id,
            "external_trigger_key_encrypted": automation.external_trigger_key_encrypted,
        }
        await session.delete(automation)
        await session.commit()
    return snapshot


async def list_automations(
    *,
    workspace_id: uuid.UUID,
    current_user_id: uuid.UUID | None = None,
    only_mine: bool = False,
) -> list[Automation]:
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
    "VALID_SOURCES",
    "VALID_SCOPES",
    "CreateAutomationInput",
    "UpdateAutomationInput",
    "create_automation",
    "update_automation",
    "rotate_webhook_secret",
    "pause_automation",
    "resume_automation",
    "delete_automation",
    "list_automations",
    "list_runs_for_automation",
    "resolve_automation",
    "resolve_by_webhook_secret",
    "is_workspace_admin",
    "generate_webhook_secret",
]
