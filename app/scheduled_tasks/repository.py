"""Persistence + business logic for scheduled tasks.

All public functions take a `workspace_id` (UUID) so cross-tenant access is
impossible by construction; the resolver enforces this even when the caller
passes a raw UUID (which could in principle belong to another workspace).

Permission model in v1:
- local: only `owner_user_id` may update/delete/pause/resume.
- system: NO update of prompt/cron/timezone/scope; only `destination_slack_id`
  is mutable, and any workspace member can change it (we'll tighten when roles
  ship). NEVER deletable via the tool path.
- global: reserved for a future slice that introduces workspace admin roles.
  The agent tool literal restricts `scope` to 'local' so v1 paths cannot
  produce a global task; defensive guards here still cover the case if one
  ends up in the DB via seeding.

`is_workspace_admin` is stubbed to return False (not True!) until roles ship.
Returning True by default would mean every workspace member can edit every
global task in v1, which is a real permission hole, not a harmless TODO.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Literal
from zoneinfo import ZoneInfo

import structlog
from croniter import croniter
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScheduledTask, Workspace
from app.db.session import get_session
from app.scheduled_tasks.system_defaults import ALL_SYSTEM_TASKS, SystemTaskDefault
from app.scheduled_tasks.timezone import resolve_timezone

log = structlog.get_logger(__name__)


# Croniter computes next fires; for cadence validation we ensure consecutive
# fires are at least MIN_CRON_INTERVAL_S apart. Below this we reject in v1:
# the scheduler ticks every 30s and a sub-5min cron + long agent runs is a
# recipe for missed fires + thundering herds on the same task.
MIN_CRON_INTERVAL_S: int = 300
# Truncation cap on `last_run_error` per the schema. Kept centralized so a
# future schema bump only changes one constant.
MAX_LAST_RUN_ERROR_LEN: int = 1000
MAX_LAST_RUN_SUMMARY_LEN: int = 2000

# UUID v1-v5 regex. Anchored. Used by the resolver to decide UUID-lookup vs
# slug-lookup; cheap enough to run on every call.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Slug shape we expect from the tool: lowercase + digits + dashes. We don't
# reject other characters at the DB layer (the column is plain text) but we
# do reject at the tool boundary so naming stays consistent across a workspace.
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class ScheduledTaskError(Exception):
    """Base for everything in this module that the tool layer should
    catch + render as a friendly user message."""


class TaskValidationError(ScheduledTaskError):
    """User input is malformed: bad cron, sub-min cadence, unknown tz, etc."""


class TaskNotFound(ScheduledTaskError):
    """The (workspace_id, id-or-slug) pair didn't resolve to a row. Either it
    doesn't exist or it belongs to a different workspace (we don't reveal
    which on purpose)."""


class TaskPermissionError(ScheduledTaskError):
    """Caller doesn't have rights to perform this operation on this task."""


class TaskNameConflict(ScheduledTaskError):
    """A task with the same slug already exists in this workspace."""


# --------------------------------------------------------------------------- #
# Admin stub
# --------------------------------------------------------------------------- #


async def is_workspace_admin(
    session: AsyncSession, *, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> bool:
    """TODO(roles): real implementation once we add a workspace_member.role
    column (or similar). Returning False until then is the safe default:
    only creators can edit their own global tasks, and admin-only system task
    operations (delete, full update) stay blocked. Returning True here would
    open every workspace member up to editing other people's global tasks,
    which is NOT what we want.

    Args are kept in the eventual signature so callers don't change when we
    swap the body."""
    _ = (session, user_id, workspace_id)
    return False


# --------------------------------------------------------------------------- #
# Cron + timezone helpers
# --------------------------------------------------------------------------- #


def validate_cron_spec(cron_spec: str, tz_name: str) -> None:
    """Raise TaskValidationError if the cron is unparseable OR if its minimum
    cadence between consecutive fires is below MIN_CRON_INTERVAL_S. The
    cadence check uses two consecutive `get_next` calls from now in the given
    timezone -- this catches obvious sub-5min crons (`* * * * *`) without
    requiring a full schedule analysis.

    Note: this is not a complete cadence proof (a cron like `0,1 * * * *`
    fires twice within 1 minute then once an hour; the average is fine but
    the spike isn't). For T-1 the simple two-step check is enough; we can
    upgrade later if users hit edge cases."""
    if not croniter.is_valid(cron_spec):
        raise TaskValidationError(
            f"Cron `{cron_spec}` no es válido. Ejemplo: `0 9 * * 1-5` para días "
            "laborables a las 9am."
        )
    try:
        tz = ZoneInfo(tz_name)
    except Exception as exc:
        raise TaskValidationError(
            f"Timezone `{tz_name}` no es válido."
        ) from exc
    now_in_tz = datetime.now(tz)
    it = croniter(cron_spec, now_in_tz)
    first = it.get_next(datetime)
    second = it.get_next(datetime)
    interval = (second - first).total_seconds()
    if interval < MIN_CRON_INTERVAL_S:
        raise TaskValidationError(
            f"En v1 las scheduled tasks no pueden correr más seguido que cada "
            f"{MIN_CRON_INTERVAL_S // 60} minutos. Tu cron `{cron_spec}` "
            f"dispararía cada ~{int(interval)} segundos. Usá un intervalo más "
            "grande (cada 5+ minutos)."
        )


def compute_next_run_at(
    cron_spec: str, tz_name: str, *, base_utc: datetime | None = None
) -> datetime:
    """Return the next fire time as an aware UTC datetime.

    `base_utc` defaults to now(UTC). When the scheduler computes the NEXT
    next_run_at after a fire, it passes now() rather than the previous
    next_run_at -- that's the skip-not-catchup policy (see scheduler.py).
    """
    tz = ZoneInfo(tz_name)
    base = (base_utc or datetime.now(timezone.utc)).astimezone(tz)
    nxt = croniter(cron_spec, base).get_next(datetime)
    if nxt.tzinfo is None:
        # croniter sometimes returns naive datetimes depending on input shape.
        # Localize before converting to UTC so the wall-clock time is preserved.
        nxt = nxt.replace(tzinfo=tz)
    return nxt.astimezone(timezone.utc)


def count_missed_fires(
    cron_spec: str,
    tz_name: str,
    *,
    last_run_at: datetime | None,
    now_utc: datetime | None = None,
) -> int:
    """How many fires of `cron_spec` we silently skipped between `last_run_at`
    and `now_utc`. Used for the `scheduled_task_skipped_missed_fires` log
    event when the scheduler comes back online after downtime.

    Returns 0 if `last_run_at` is None (never run before) or if no fires fall
    in the window. The current fire (the one we're about to execute) is NOT
    counted -- this is purely the count of fires we DIDN'T do.
    """
    if last_run_at is None:
        return 0
    tz = ZoneInfo(tz_name)
    now_utc = now_utc or datetime.now(timezone.utc)
    base = last_run_at.astimezone(tz)
    end = now_utc.astimezone(tz)
    it = croniter(cron_spec, base)
    total = 0
    # Walk fires forward from last_run_at; count every fire that's <= now.
    # The most recent of those is the fire we're about to execute now ("the
    # current fire"), which is NOT a missed fire -- so we subtract 1 from
    # the total at the end. Hard cap at 10000 to bound runaway loops.
    for _ in range(10000):
        nxt = it.get_next(datetime)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=tz)
        if nxt > end:
            break
        total += 1
    return max(0, total - 1)


# --------------------------------------------------------------------------- #
# Resolver (workspace-scoped, UUID or slug)
# --------------------------------------------------------------------------- #


async def resolve_task(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    task_id_or_name: str,
) -> ScheduledTask:
    """Return the task in this workspace matching either its UUID or its
    slug name. ALWAYS filters by workspace_id even on UUID lookups -- the
    caller may legitimately know a UUID but not be in that workspace, and we
    must not leak across tenants.

    Raises TaskNotFound (no information about whether the row exists in a
    different workspace) if nothing matches.
    """
    handle = (task_id_or_name or "").strip()
    if not handle:
        raise TaskNotFound("No me pasaste id ni nombre de la task.")

    if _UUID_RE.match(handle):
        try:
            task_id = uuid.UUID(handle)
        except ValueError:
            # Regex matched but uuid.UUID still rejected -- defensive, should
            # not happen given the regex. Fall through to slug lookup.
            task_id = None
        if task_id is not None:
            row = (
                await session.execute(
                    select(ScheduledTask).where(
                        ScheduledTask.id == task_id,
                        ScheduledTask.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                return row
            # Intentionally fall through to slug lookup in case the user
            # named their task with a literal UUID slug. Unlikely but cheap.

    row = (
        await session.execute(
            select(ScheduledTask).where(
                ScheduledTask.name == handle,
                ScheduledTask.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise TaskNotFound(
            f"No encontré ninguna scheduled task llamada `{handle}` en este workspace."
        )
    return row


# --------------------------------------------------------------------------- #
# Permission helpers
# --------------------------------------------------------------------------- #


async def _ensure_can_modify(
    session: AsyncSession,
    task: ScheduledTask,
    *,
    current_user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    field_being_changed: str | None = None,
) -> None:
    """Raise TaskPermissionError if `current_user_id` cannot modify `task`.

    `field_being_changed` lets us allow the system-task carveout: any user
    can change `destination_slack_id` on a system task in v1, but nothing else.
    Pass None for full-update / delete permission checks; pass the specific
    field name for granular checks (used by `update_scheduled_task`)."""
    if task.scope == "local":
        if task.owner_user_id != current_user_id:
            raise TaskPermissionError(
                "No podés editar una scheduled task local de otro usuario. "
                "Pedile al dueño que la modifique."
            )
        return

    if task.scope == "system":
        if field_being_changed == "destination_slack_id":
            return
        raise TaskPermissionError(
            "Las scheduled tasks del sistema no se pueden modificar "
            "(solo cambiar a qué canal postean, o pausarlas)."
        )

    # scope == 'global': v1 has no path to create one, but if one exists
    # (manual seed), allow only creator or admin.
    if task.scope == "global":
        if task.created_by_user_id == current_user_id:
            return
        if await is_workspace_admin(
            session, user_id=current_user_id, workspace_id=workspace_id
        ):
            return
        raise TaskPermissionError(
            "Solo el creador (o un admin del workspace) puede modificar una "
            "scheduled task global."
        )

    # Unknown scope: refuse safely.
    raise TaskPermissionError(
        f"Scope desconocido `{task.scope}`; no puedo decidir permisos."
    )


async def _ensure_can_pause(
    session: AsyncSession,
    task: ScheduledTask,
    *,
    current_user_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> None:
    """Pause/resume policy: more permissive than full update.

    - local: only owner.
    - system: any workspace member (so a non-admin can silence a noisy
      daily-brief without waiting for an admin).
    - global: creator or admin (same as modify).
    """
    if task.scope == "local":
        if task.owner_user_id != current_user_id:
            raise TaskPermissionError(
                "Solo el dueño puede pausar una scheduled task local."
            )
        return
    if task.scope == "system":
        return  # any member
    if task.scope == "global":
        if task.created_by_user_id == current_user_id:
            return
        if await is_workspace_admin(
            session, user_id=current_user_id, workspace_id=workspace_id
        ):
            return
        raise TaskPermissionError(
            "Solo el creador (o un admin del workspace) puede pausar una "
            "scheduled task global."
        )
    raise TaskPermissionError(
        f"Scope desconocido `{task.scope}`; no puedo decidir permisos."
    )


async def _ensure_can_delete(
    session: AsyncSession,
    task: ScheduledTask,
    *,
    current_user_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> None:
    if task.scope == "system":
        raise TaskPermissionError(
            "Las scheduled tasks del sistema no se pueden borrar (solo "
            "pausar). Si te molestan, decime 'pausá la task X'."
        )
    # Same rules as modify for local + global.
    await _ensure_can_modify(
        session,
        task,
        current_user_id=current_user_id,
        workspace_id=workspace_id,
    )


# --------------------------------------------------------------------------- #
# Create / update / delete / pause / resume
# --------------------------------------------------------------------------- #


@dataclass
class CreateTaskInput:
    workspace_id: uuid.UUID
    created_by_user_id: uuid.UUID
    name: str
    prompt: str
    cron_spec: str
    timezone: str  # IANA name (already resolved by the tool layer)
    scope: Literal["local"]  # v1: only 'local' from the tool path
    destination_type: Literal["channel", "dm"]
    destination_slack_id: str


async def create_task(payload: CreateTaskInput) -> ScheduledTask:
    """Create a local scheduled task. Validates the slug shape, the cron,
    the timezone, and the workspace-scoped name uniqueness. Computes
    `next_run_at` from the cron + tz before persisting."""
    if not _SLUG_RE.match(payload.name):
        raise TaskValidationError(
            f"Nombre `{payload.name}` inválido: usá kebab-case (letras minúsculas, "
            "números y guiones; entre 2 y 64 chars, sin guion al inicio/fin)."
        )

    # Cron + tz validation: tz must be IANA, cron must parse and respect the
    # min cadence. The tool layer already passed us an IANA name; we revalidate
    # here so direct callers (tests) can't bypass.
    validate_cron_spec(payload.cron_spec, payload.timezone)
    next_run = compute_next_run_at(payload.cron_spec, payload.timezone)

    async with get_session() as session:
        existing = (
            await session.execute(
                select(ScheduledTask.id).where(
                    ScheduledTask.workspace_id == payload.workspace_id,
                    ScheduledTask.name == payload.name,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise TaskNameConflict(
                f"Ya existe una scheduled task llamada `{payload.name}` en este "
                "workspace. Pickeá otro nombre."
            )

        task = ScheduledTask(
            workspace_id=payload.workspace_id,
            scope="local",
            created_by_user_id=payload.created_by_user_id,
            owner_user_id=payload.created_by_user_id,
            name=payload.name,
            prompt=payload.prompt,
            cron_spec=payload.cron_spec,
            timezone=payload.timezone,
            destination_type=payload.destination_type,
            destination_slack_id=payload.destination_slack_id,
            is_paused=False,
            next_run_at=next_run,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

    log.info(
        "scheduled_task_created",
        task_id=str(task.id),
        workspace_id=str(task.workspace_id),
        scope=task.scope,
        name=task.name,
        created_by_user_id=str(task.created_by_user_id),
        next_run_at=task.next_run_at.isoformat() if task.next_run_at else None,
    )
    return task


@dataclass
class UpdateTaskInput:
    workspace_id: uuid.UUID
    current_user_id: uuid.UUID
    task_id_or_name: str
    prompt: str | None = None
    cron_spec: str | None = None
    timezone: str | None = None
    destination_type: Literal["channel", "dm"] | None = None
    destination_slack_id: str | None = None


async def update_task(payload: UpdateTaskInput) -> ScheduledTask:
    """Apply partial updates. Permission-checked per field where it matters
    (system tasks can only have destination changed). Recomputes next_run_at
    if cron_spec or timezone changes."""
    async with get_session() as session:
        task = await resolve_task(session, payload.workspace_id, payload.task_id_or_name)

        # Build the set of fields being changed up-front so we can run granular
        # permission checks. We check `destination_slack_id` separately because
        # the system-task carveout depends on it.
        changing: dict[str, object] = {}
        if payload.prompt is not None and payload.prompt != task.prompt:
            changing["prompt"] = payload.prompt
        if payload.cron_spec is not None and payload.cron_spec != task.cron_spec:
            changing["cron_spec"] = payload.cron_spec
        if payload.timezone is not None and payload.timezone != task.timezone:
            changing["timezone"] = payload.timezone
        if payload.destination_type is not None and payload.destination_type != task.destination_type:
            changing["destination_type"] = payload.destination_type
        if (
            payload.destination_slack_id is not None
            and payload.destination_slack_id != task.destination_slack_id
        ):
            changing["destination_slack_id"] = payload.destination_slack_id

        if not changing:
            return task  # no-op: nothing to update

        # Permission gates:
        # - Any non-destination change requires the full modify permission.
        # - destination_slack_id alone passes the system-task carveout.
        non_destination_changes = [k for k in changing.keys() if k != "destination_slack_id"]
        if non_destination_changes:
            await _ensure_can_modify(
                session, task,
                current_user_id=payload.current_user_id,
                workspace_id=payload.workspace_id,
                field_being_changed=None,
            )
        elif "destination_slack_id" in changing:
            await _ensure_can_modify(
                session, task,
                current_user_id=payload.current_user_id,
                workspace_id=payload.workspace_id,
                field_being_changed="destination_slack_id",
            )

        # If cron or tz changes, validate the new combination together (the
        # cadence check needs both) and recompute next_run_at.
        new_cron = changing.get("cron_spec", task.cron_spec)
        new_tz = changing.get("timezone", task.timezone)
        if "cron_spec" in changing or "timezone" in changing:
            validate_cron_spec(str(new_cron), str(new_tz))
            task.next_run_at = compute_next_run_at(str(new_cron), str(new_tz))

        for field, value in changing.items():
            setattr(task, field, value)

        await session.commit()
        await session.refresh(task)

    log.info(
        "scheduled_task_updated",
        task_id=str(task.id),
        workspace_id=str(task.workspace_id),
        fields_changed=list(changing.keys()),
    )
    return task


async def delete_task(
    workspace_id: uuid.UUID,
    current_user_id: uuid.UUID,
    task_id_or_name: str,
) -> str:
    """Delete a task. System tasks cannot be deleted; the tool will catch
    TaskPermissionError and render a friendly message. Returns the deleted
    task's name for log/UX."""
    async with get_session() as session:
        task = await resolve_task(session, workspace_id, task_id_or_name)
        await _ensure_can_delete(
            session, task,
            current_user_id=current_user_id,
            workspace_id=workspace_id,
        )
        deleted_name = task.name
        await session.delete(task)
        await session.commit()

    log.info(
        "scheduled_task_deleted",
        task_id=task_id_or_name,
        workspace_id=str(workspace_id),
        deleted_by_user_id=str(current_user_id),
        deleted_name=deleted_name,
    )
    return deleted_name


async def pause_task(
    workspace_id: uuid.UUID,
    current_user_id: uuid.UUID,
    task_id_or_name: str,
    until: datetime | None = None,
) -> ScheduledTask:
    """Pause a task indefinitely (until=None) or until a future timestamp.
    If `until` is in the past, we still set it but the scheduler will treat
    the task as already auto-resumed on its next tick -- so a "pause until
    yesterday" effectively does nothing, which is the right idempotent
    behavior for callers who pass a stale date."""
    async with get_session() as session:
        task = await resolve_task(session, workspace_id, task_id_or_name)
        await _ensure_can_pause(
            session, task,
            current_user_id=current_user_id,
            workspace_id=workspace_id,
        )
        task.is_paused = True
        task.paused_until = until
        await session.commit()
        await session.refresh(task)

    log.info(
        "scheduled_task_paused",
        task_id=str(task.id),
        workspace_id=str(workspace_id),
        paused_until=until.isoformat() if until else None,
    )
    return task


async def resume_task(
    workspace_id: uuid.UUID,
    current_user_id: uuid.UUID,
    task_id_or_name: str,
) -> ScheduledTask:
    """Resume a paused task and recompute next_run_at from now()."""
    async with get_session() as session:
        task = await resolve_task(session, workspace_id, task_id_or_name)
        await _ensure_can_pause(
            session, task,
            current_user_id=current_user_id,
            workspace_id=workspace_id,
        )
        task.is_paused = False
        task.paused_until = None
        task.next_run_at = compute_next_run_at(task.cron_spec, task.timezone)
        await session.commit()
        await session.refresh(task)

    log.info(
        "scheduled_task_resumed",
        task_id=str(task.id),
        workspace_id=str(workspace_id),
        new_next_run_at=task.next_run_at.isoformat() if task.next_run_at else None,
    )
    return task


# --------------------------------------------------------------------------- #
# Listing
# --------------------------------------------------------------------------- #


async def list_tasks(
    workspace_id: uuid.UUID,
    current_user_id: uuid.UUID,
    *,
    filter_mode: Literal["mine", "all", "system"] = "mine",
) -> list[ScheduledTask]:
    """Filtered task listing.

    - mine: scope='local' AND owner_user_id=current_user.
    - all: union of the user's own locals + every system task. Global tasks
      would join here too once v1 enables them; for now there shouldn't be
      any in the DB.
    - system: scope='system' only.

    Always filters by workspace_id. Stable order: scope (system first), then
    name, so the agent's listing output is deterministic across calls.
    """
    base = select(ScheduledTask).where(ScheduledTask.workspace_id == workspace_id)

    if filter_mode == "mine":
        stmt = base.where(
            and_(
                ScheduledTask.scope == "local",
                ScheduledTask.owner_user_id == current_user_id,
            )
        )
    elif filter_mode == "system":
        stmt = base.where(ScheduledTask.scope == "system")
    else:  # "all"
        stmt = base.where(
            or_(
                and_(
                    ScheduledTask.scope == "local",
                    ScheduledTask.owner_user_id == current_user_id,
                ),
                ScheduledTask.scope == "system",
                # global tasks (when they exist): visible to all members
                ScheduledTask.scope == "global",
            )
        )
    stmt = stmt.order_by(ScheduledTask.scope, ScheduledTask.name)

    async with get_session() as session:
        rows = (await session.execute(stmt)).scalars().all()
        return list(rows)


# --------------------------------------------------------------------------- #
# Seeding (system tasks)
# --------------------------------------------------------------------------- #


async def seed_system_tasks_for_workspace(
    workspace_id: uuid.UUID,
    bot_home_channel_id: str | None,
    *,
    defaults: Iterable[SystemTaskDefault] = ALL_SYSTEM_TASKS,
) -> int:
    """Idempotently insert the workspace's system tasks. Uses INSERT ... ON
    CONFLICT (workspace_id, name) DO NOTHING so multiple API instances racing
    at startup don't trip the UNIQUE constraint -- they just no-op.

    Returns the number of rows actually inserted (0 on a no-op re-seed). The
    caller can log this. `bot_home_channel_id` may be None; the row is
    persisted anyway with a NULL destination, and the scheduler treats that
    as "skip + mark failed" until admin configures the channel.
    """
    inserted = 0
    now_utc = datetime.now(timezone.utc)
    for default in defaults:
        next_run = compute_next_run_at(default.cron_spec, default.timezone, base_utc=now_utc)
        stmt = (
            pg_insert(ScheduledTask)
            .values(
                workspace_id=workspace_id,
                scope="system",
                created_by_user_id=None,
                owner_user_id=None,
                name=default.name,
                prompt=default.prompt,
                cron_spec=default.cron_spec,
                timezone=default.timezone,
                destination_type=default.destination_type,
                destination_slack_id=bot_home_channel_id,
                is_paused=False,
                paused_until=None,
                next_run_at=next_run,
            )
            .on_conflict_do_nothing(
                constraint="uq_scheduled_task_workspace_name",
            )
        )
        async with get_session() as session:
            result = await session.execute(stmt)
            await session.commit()
            if (result.rowcount or 0) > 0:
                inserted += 1
                log.info(
                    "scheduled_task_seeded",
                    workspace_id=str(workspace_id),
                    name=default.name,
                    cron_spec=default.cron_spec,
                    destination_slack_id=bot_home_channel_id,
                )
    return inserted


async def seed_system_tasks_for_all_workspaces() -> int:
    """Startup hook: iterate every installed workspace and (idempotently)
    seed its system tasks. Returns total rows inserted across all workspaces
    -- typically 0 in steady state; nonzero only on a fresh deploy that
    added a new system task or on workspaces that pre-date this slice.
    """
    total = 0
    async with get_session() as session:
        # Only workspaces that are actually installed (have a bot_token);
        # workspace rows created from stray events without a token aren't
        # seedable (the bot can't post anywhere).
        rows = (
            await session.execute(
                select(
                    Workspace.id,
                    Workspace.bot_home_channel_id,
                ).where(Workspace.installed_at.isnot(None))
            )
        ).all()

    for ws_id, channel_id in rows:
        try:
            total += await seed_system_tasks_for_workspace(ws_id, channel_id)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "scheduled_task_seed_failed",
                workspace_id=str(ws_id),
                error=str(exc),
            )
    if total:
        log.info("scheduled_task_seed_startup", inserted=total, workspaces=len(rows))
    return total


# --------------------------------------------------------------------------- #
# Scheduler-facing helpers
# --------------------------------------------------------------------------- #


async def claim_due_tasks(
    session: AsyncSession,
    *,
    limit: int = 100,
    now_utc: datetime | None = None,
) -> list[ScheduledTask]:
    """Within the caller's open transaction, SELECT ... FOR UPDATE SKIP LOCKED
    up to `limit` due tasks. The caller is responsible for committing /
    rolling back; the rows stay locked until then, which is what gives us
    cross-worker idempotency without a separate advisory lock.

    Eligibility:
      - next_run_at <= now()
      - AND (not paused, OR paused_until has passed)
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    stmt = (
        select(ScheduledTask)
        .where(
            ScheduledTask.next_run_at.isnot(None),
            ScheduledTask.next_run_at <= now_utc,
            or_(
                ScheduledTask.is_paused.is_(False),
                and_(
                    ScheduledTask.paused_until.isnot(None),
                    ScheduledTask.paused_until <= now_utc,
                ),
            ),
        )
        .order_by(ScheduledTask.next_run_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.execute(stmt)).scalars().all())


async def record_fire_started(
    session: AsyncSession,
    task: ScheduledTask,
    *,
    now_utc: datetime | None = None,
) -> int:
    """Inside the caller's transaction, advance the task to the 'running'
    state and compute its next next_run_at. Returns the number of fires we
    silently skipped (catchup-skip policy: scheduler down for hours means
    one fire now, the missed N are logged but not executed).

    The caller commits the transaction after this returns; the row stays
    locked under FOR UPDATE until then.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    missed = count_missed_fires(
        task.cron_spec,
        task.timezone,
        last_run_at=task.last_run_at,
        now_utc=now_utc,
    )
    # Auto-resume the row if it was paused-until-a-past-date: the eligibility
    # filter let it through, but the column should also reflect that it's no
    # longer paused.
    if task.is_paused and task.paused_until is not None and task.paused_until <= now_utc:
        task.is_paused = False
        task.paused_until = None

    task.last_run_at = now_utc
    task.last_run_status = "running"
    task.last_run_error = None
    task.next_run_at = compute_next_run_at(task.cron_spec, task.timezone, base_utc=now_utc)
    # session.flush so the row state is visible to the caller's transaction;
    # commit is the caller's responsibility.
    await session.flush()
    return missed


async def record_fire_finished(
    task_id: uuid.UUID,
    *,
    status: Literal["success", "failed"],
    summary: str | None = None,
    error: str | None = None,
) -> None:
    """Update the row after the agent run completes. Opens its own session
    (the scheduler's claim transaction is long gone by the time the agent
    finishes). Best-effort: a failure here only loses observability, not
    the run itself."""
    assert status in ("success", "failed")
    if summary and len(summary) > MAX_LAST_RUN_SUMMARY_LEN:
        summary = summary[:MAX_LAST_RUN_SUMMARY_LEN].rstrip() + "…"
    if error and len(error) > MAX_LAST_RUN_ERROR_LEN:
        error = error[:MAX_LAST_RUN_ERROR_LEN].rstrip() + "…"
    async with get_session() as session:
        row = (
            await session.execute(
                select(ScheduledTask).where(ScheduledTask.id == task_id)
            )
        ).scalar_one_or_none()
        if row is None:
            log.warning("scheduled_task_finish_row_missing", task_id=str(task_id))
            return
        row.last_run_status = status
        row.last_run_error = error if status == "failed" else None
        if status == "success" and summary is not None:
            row.last_run_summary = summary
        await session.commit()


__all__ = [
    # Errors
    "ScheduledTaskError",
    "TaskValidationError",
    "TaskNotFound",
    "TaskPermissionError",
    "TaskNameConflict",
    # CRUD
    "CreateTaskInput",
    "UpdateTaskInput",
    "create_task",
    "update_task",
    "delete_task",
    "pause_task",
    "resume_task",
    "list_tasks",
    "resolve_task",
    # Validation / cron
    "validate_cron_spec",
    "compute_next_run_at",
    "count_missed_fires",
    "MIN_CRON_INTERVAL_S",
    # Permissions
    "is_workspace_admin",
    # Seeding
    "seed_system_tasks_for_workspace",
    "seed_system_tasks_for_all_workspaces",
    # Scheduler-facing
    "claim_due_tasks",
    "record_fire_started",
    "record_fire_finished",
]
