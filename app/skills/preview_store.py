"""Postgres-backed store for skill upload previews.

Previously the previews lived in a process-local dict. That meant every
Render redeploy / restart / scaling event wiped the cache and produced
'La preview venció' errors when users clicked Install on a stale block-kit.
The cache is now a real Postgres table (`skill_preview`), scoped to
(workspace_id, app_user_id), with a 30-minute TTL and a background sweep.

Note on timezone handling: the `skill_preview.expires_at` column is
`TIMESTAMP WITHOUT TIME ZONE` (matches the rest of the schema). asyncpg
rejects binding a tz-aware datetime against a naive column, so we strip
tzinfo on writes. Comparisons against `datetime.utcnow()`-equivalent
naive values stay correct (everything is naive UTC).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import delete

from app.db.models import SkillPreview
from app.db.session import get_session

log = structlog.get_logger(__name__)

# How long a preview survives between upload and the user's click. Matches
# what the in-memory dict used; long enough for an interactive flow, short
# enough that storage stays bounded.
PREVIEW_TTL = timedelta(minutes=30)


def _now_naive() -> datetime:
    """Naive UTC, matching the TIMESTAMP WITHOUT TIME ZONE columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def create_preview(
    *,
    workspace_id: uuid.UUID,
    app_user_id: uuid.UUID,
    slack_user_id: str,
    channel_id: str,
    filename: str,
    name: str,
    description: str,
    activation: str,
    body: str,
    links: list[str],
    inferred_fields: list[str],
) -> uuid.UUID:
    """Insert a preview row. Returns its UUID (used as `preview_id` in the
    block-kit `action_id` so each button click maps back to the row)."""
    row = SkillPreview(
        workspace_id=workspace_id,
        app_user_id=app_user_id,
        slack_user_id=slack_user_id,
        channel_id=channel_id,
        filename=filename,
        name=name,
        description=description,
        activation=activation,
        body=body,
        links=list(links),
        inferred_fields=list(inferred_fields),
        expires_at=_now_naive() + PREVIEW_TTL,
    )
    async with get_session() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    log.info(
        "skill_preview_created",
        preview_id=str(row.id),
        workspace_id=str(workspace_id),
        app_user_id=str(app_user_id),
        bytes=len(body),
    )
    return row.id


async def get_preview(preview_id: uuid.UUID) -> SkillPreview | None:
    """Return the preview if it exists AND has not expired. Callers see
    None as 'preview venció' (gone forever; user must re-upload)."""
    async with get_session() as session:
        row = await session.get(SkillPreview, preview_id)
    if row is None:
        return None
    if row.expires_at < _now_naive():
        # Tolerate stale rows in the DB: cleanup_expired will sweep them.
        return None
    return row


async def update_preview(
    preview_id: uuid.UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    activation: str | None = None,
    inferred_fields: list[str] | None = None,
) -> SkillPreview | None:
    """Mutate the editable fields (used by the Edit modal submit handler).
    Returns the refreshed row, or None if the preview is gone or expired."""
    async with get_session() as session:
        row = await session.get(SkillPreview, preview_id)
        if row is None or row.expires_at < _now_naive():
            return None
        if name is not None:
            row.name = name
        if description is not None:
            row.description = description
        if activation is not None:
            row.activation = activation
        if inferred_fields is not None:
            row.inferred_fields = list(inferred_fields)
        await session.commit()
        await session.refresh(row)
    return row


async def delete_preview(preview_id: uuid.UUID) -> None:
    """Remove a preview after Install completion or Cancel. Idempotent:
    a missing row is a no-op (delete by id doesn't error)."""
    async with get_session() as session:
        await session.execute(
            delete(SkillPreview).where(SkillPreview.id == preview_id)
        )
        await session.commit()


async def cleanup_expired() -> int:
    """Sweep rows past their `expires_at`. Returns count deleted (for
    structured logging). Run periodically from the app lifespan."""
    async with get_session() as session:
        result = await session.execute(
            delete(SkillPreview).where(SkillPreview.expires_at < _now_naive())
        )
        await session.commit()
    return result.rowcount or 0
