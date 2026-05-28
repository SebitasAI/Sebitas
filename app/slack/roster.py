"""Per-workspace roster of Slack users + channel members.

Three sync mechanisms combined:
- Lazy on-first-use: ensure_workspace_synced() runs full users.list if the
  workspace has no rows yet (or rows older than the stale window).
- Periodic refresh: a background task in lifespan reruns sync every 12h.
- On-miss: find_user() that fails forces a refresh and retries once.

Slack scopes required (must be added to the app + reinstalled):
- users:read, users:read.email
- channels:read, groups:read, mpim:read, im:read (for conversations.members)

Tenant scope is strict: every read/write filters by workspace_id. A roster
lookup cannot leak across workspaces by construction.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import select

from app.config import get_settings
from app.db.models import SlackChannel, SlackUser
from app.db.session import get_session

log = structlog.get_logger(__name__)

# After how long a workspace roster is considered stale and we refresh.
_USERS_STALE_AFTER = timedelta(hours=12)
_CHANNEL_STALE_AFTER = timedelta(hours=1)  # channel membership shifts faster


def _client() -> AsyncWebClient:
    return AsyncWebClient(token=get_settings().slack_bot_token)


# --------------------------------------------------------------------------- #
# Workspace sync
# --------------------------------------------------------------------------- #


async def _users_last_sync(workspace_id: uuid.UUID) -> datetime | None:
    async with get_session() as session:
        row = (
            await session.execute(
                select(SlackUser.last_synced_at)
                .where(SlackUser.workspace_id == workspace_id)
                .order_by(SlackUser.last_synced_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    return row


async def sync_workspace_users(workspace_id: uuid.UUID, client: AsyncWebClient | None = None) -> int:
    """Paginated users.list -> upsert into slack_user. Returns the row count
    seen. Idempotent: rerunning updates last_synced_at + any changed fields."""
    c = client or _client()
    cursor: str | None = None
    seen_user_ids: list[str] = []
    upserts = 0
    while True:
        params: dict = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        try:
            resp = await c.users_list(**params)
        except Exception as exc:  # noqa: BLE001
            log.warning("roster_users_list_failed", workspace_id=str(workspace_id), error=str(exc))
            break
        members = resp.get("members") or []
        for m in members:
            uid = m.get("id")
            if not uid:
                continue
            seen_user_ids.append(uid)
            profile = m.get("profile") or {}
            display_name = profile.get("display_name") or profile.get("display_name_normalized") or None
            real_name = m.get("real_name") or profile.get("real_name_normalized") or None
            email = profile.get("email")
            is_bot = bool(m.get("is_bot")) or uid == "USLACKBOT"
            deleted = bool(m.get("deleted"))
            await _upsert_user(
                workspace_id=workspace_id,
                slack_user_id=uid,
                display_name=display_name,
                real_name=real_name,
                email=email,
                is_bot=is_bot,
                deleted=deleted,
            )
            upserts += 1
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    log.info("roster_users_synced", workspace_id=str(workspace_id), count=upserts)
    return upserts


async def _upsert_user(*, workspace_id, slack_user_id, display_name, real_name, email, is_bot, deleted) -> None:
    async with get_session() as session:
        row = (
            await session.execute(
                select(SlackUser).where(
                    SlackUser.workspace_id == workspace_id,
                    SlackUser.slack_user_id == slack_user_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(SlackUser(
                workspace_id=workspace_id,
                slack_user_id=slack_user_id,
                display_name=display_name, real_name=real_name, email=email,
                is_bot=is_bot, deleted=deleted,
            ))
        else:
            row.display_name = display_name
            row.real_name = real_name
            row.email = email
            row.is_bot = is_bot
            row.deleted = deleted
            row.last_synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.commit()


async def ensure_workspace_synced(workspace_id: uuid.UUID, *, force: bool = False) -> None:
    """If the workspace has never been synced (or it's stale), run a full
    users.list sync. Idempotent + low-cost on warm caches."""
    if not force:
        last = await _users_last_sync(workspace_id)
        if last is not None:
            # `last` is naive; treat as UTC.
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if now - last < _USERS_STALE_AFTER:
                return
    await sync_workspace_users(workspace_id)


# --------------------------------------------------------------------------- #
# Channel membership
# --------------------------------------------------------------------------- #


async def get_channel_members(
    workspace_id: uuid.UUID,
    slack_channel_id: str,
    *,
    limit: int = 50,
    force: bool = False,
) -> list[dict]:
    """Returns up to `limit` channel members with (slack_user_id, display_name,
    real_name). Caches the membership list in slack_channel.members; refreshes
    if older than 1h or force=True. Excludes deleted users + bots."""
    async with get_session() as session:
        row = (
            await session.execute(
                select(SlackChannel).where(
                    SlackChannel.workspace_id == workspace_id,
                    SlackChannel.slack_channel_id == slack_channel_id,
                )
            )
        ).scalar_one_or_none()
    needs_refresh = (
        force or row is None or row.members is None
        or (datetime.now(timezone.utc).replace(tzinfo=None) - row.last_synced_at > _CHANNEL_STALE_AFTER)
    )
    if needs_refresh:
        await _refresh_channel(workspace_id, slack_channel_id)
        async with get_session() as session:
            row = (
                await session.execute(
                    select(SlackChannel).where(
                        SlackChannel.workspace_id == workspace_id,
                        SlackChannel.slack_channel_id == slack_channel_id,
                    )
                )
            ).scalar_one_or_none()

    if row is None or not row.members:
        return []
    # Hydrate display_name / real_name from slack_user. We INCLUDE bots/apps:
    # mentioning another app is a legit trigger (bot-to-bot collaboration in
    # shared channels). Only `deleted` users are filtered.
    async with get_session() as session:
        users = (
            await session.execute(
                select(SlackUser).where(
                    SlackUser.workspace_id == workspace_id,
                    SlackUser.slack_user_id.in_(row.members),
                    SlackUser.deleted == False,  # noqa: E712
                )
            )
        ).scalars().all()
    out = [
        {
            "slack_user_id": u.slack_user_id,
            "display_name": u.display_name,
            "real_name": u.real_name,
            "is_bot": u.is_bot,
        }
        for u in users[:limit]
    ]
    return out


async def _refresh_channel(workspace_id: uuid.UUID, slack_channel_id: str) -> None:
    c = _client()
    # Pull members (paginated) + metadata in one shot.
    member_ids: list[str] = []
    cursor: str | None = None
    while True:
        params: dict = {"channel": slack_channel_id, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        try:
            resp = await c.conversations_members(**params)
        except Exception as exc:  # noqa: BLE001
            log.warning("roster_channel_members_failed", channel=slack_channel_id, error=str(exc))
            return
        member_ids.extend(resp.get("members") or [])
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    name = None
    try:
        info = await c.conversations_info(channel=slack_channel_id)
        name = (info.get("channel") or {}).get("name")
    except Exception as exc:  # noqa: BLE001
        log.warning("roster_channel_info_failed", channel=slack_channel_id, error=str(exc))

    async with get_session() as session:
        row = (
            await session.execute(
                select(SlackChannel).where(
                    SlackChannel.workspace_id == workspace_id,
                    SlackChannel.slack_channel_id == slack_channel_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(SlackChannel(
                workspace_id=workspace_id,
                slack_channel_id=slack_channel_id,
                name=name,
                members=member_ids,
            ))
        else:
            row.name = name or row.name
            row.members = member_ids
            row.last_synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.commit()
    log.info("roster_channel_synced", channel=slack_channel_id, members=len(member_ids))


# --------------------------------------------------------------------------- #
# Lookup
# --------------------------------------------------------------------------- #


async def find_user(workspace_id: uuid.UUID, query: str) -> list[dict]:
    """Case-insensitive match against display_name, real_name, and email-prefix.
    Returns 0/1/many. On miss, forces a workspace re-sync and retries once."""
    matches = await _find_user_query(workspace_id, query)
    if not matches:
        # On-miss refresh: maybe the user joined since the last sync.
        await sync_workspace_users(workspace_id)
        matches = await _find_user_query(workspace_id, query)
    return matches


async def _find_user_query(workspace_id: uuid.UUID, query: str) -> list[dict]:
    q = (query or "").strip().lower()
    if not q:
        return []
    async with get_session() as session:
        rows = (
            await session.execute(
                select(SlackUser).where(
                    SlackUser.workspace_id == workspace_id,
                    SlackUser.deleted == False,  # noqa: E712
                )
            )
        ).scalars().all()
    out: list[dict] = []
    for r in rows:
        if _matches(q, r):
            out.append({
                "slack_user_id": r.slack_user_id,
                "display_name": r.display_name,
                "real_name": r.real_name,
                "email": r.email,
                "is_bot": r.is_bot,
            })
    return out


def _matches(q: str, r: SlackUser) -> bool:
    """Case-insensitive: q in display_name OR real_name OR email-prefix."""
    if r.display_name and q in r.display_name.lower():
        return True
    if r.real_name and q in r.real_name.lower():
        return True
    if r.email:
        prefix = r.email.split("@", 1)[0].lower()
        if q == r.email.lower() or q in prefix:
            return True
    return False


async def find_channel(workspace_id: uuid.UUID, name: str) -> str | None:
    """Resolve `#name` -> channel id. None if not found / ambiguous."""
    q = (name or "").strip().lstrip("#").lower()
    if not q:
        return None
    async with get_session() as session:
        rows = (
            await session.execute(
                select(SlackChannel).where(SlackChannel.workspace_id == workspace_id)
            )
        ).scalars().all()
    matches = [r for r in rows if (r.name or "").lower() == q]
    if len(matches) == 1:
        return matches[0].slack_channel_id
    return None


# --------------------------------------------------------------------------- #
# Periodic refresh (called from lifespan)
# --------------------------------------------------------------------------- #


async def periodic_refresh_loop() -> None:
    """Refresh every workspace's roster every 12h. Survives transient errors."""
    while True:
        try:
            await asyncio.sleep(12 * 3600)
            async with get_session() as session:
                # Use raw SQL via session.execute to keep this module short.
                from app.db.models import Workspace

                ws_ids = (
                    await session.execute(select(Workspace.id))
                ).scalars().all()
            for ws_id in ws_ids:
                try:
                    await sync_workspace_users(ws_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("roster_refresh_failed", workspace_id=str(ws_id), error=str(exc))
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("roster_refresh_loop_error", error=str(exc))
