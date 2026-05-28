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

from app.db.models import SlackChannel, SlackUser
from app.db.session import get_session

log = structlog.get_logger(__name__)

# After how long a workspace roster is considered stale and we refresh.
_USERS_STALE_AFTER = timedelta(hours=12)
_CHANNEL_STALE_AFTER = timedelta(hours=1)  # channel membership shifts faster

# In-process dedupe: one workspace sync at a time. Multiple callers (a
# fresh-workspace first message, plus a follow-up find_user) share the same
# Event so they don't kick off duplicate users.list paginations.
_syncing: dict[uuid.UUID, asyncio.Event] = {}
_syncing_lock = asyncio.Lock()


async def _client_for_workspace(workspace_id: uuid.UUID) -> AsyncWebClient | None:
    """Per-workspace Slack client. Returns None if the workspace isn't
    installed (no bot_token) -- callers no-op gracefully."""
    from app.slack.tokens import get_bot_token_by_workspace
    pair = await get_bot_token_by_workspace(workspace_id)
    if pair is None:
        return None
    return AsyncWebClient(token=pair[0])


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
    c = client or await _client_for_workspace(workspace_id)
    if c is None:
        log.warning("roster_sync_skipped_no_token", workspace_id=str(workspace_id))
        return 0
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


async def _start_sync_in_background(workspace_id: uuid.UUID) -> asyncio.Event:
    """Spawn (or join) a background users.list sync for this workspace.
    Returns the Event that fires when the sync completes -- callers can
    `await evt.wait()` with a timeout for bounded-wait, OR just throw away
    the reference for fire-and-forget. Idempotent: a second call while a
    sync is in flight returns the SAME Event (no duplicate paginated calls).
    """
    async with _syncing_lock:
        existing = _syncing.get(workspace_id)
        if existing is not None:
            return existing
        evt = asyncio.Event()
        _syncing[workspace_id] = evt

    async def _runner():
        try:
            await sync_workspace_users(workspace_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("workspace_sync_bg_failed", workspace_id=str(workspace_id), error=str(exc))
        finally:
            async with _syncing_lock:
                _syncing.pop(workspace_id, None)
            evt.set()

    asyncio.create_task(_runner())
    return evt


async def ensure_workspace_synced(workspace_id: uuid.UUID, *, force: bool = False) -> None:
    """Non-blocking. Kicks off a background sync if the workspace has never
    been synced (or is stale), then returns immediately. The first message
    in a fresh workspace does NOT wait for users.list to paginate -- which
    could otherwise add 10-30s of latency on large orgs and time the run
    out before the reply posts. Subsequent calls within the stale window
    are no-ops."""
    if not force:
        last = await _users_last_sync(workspace_id)
        if last is not None:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if now - last < _USERS_STALE_AFTER:
                return
    # Fire-and-forget: we discard the Event; the background task signals it
    # but no one in this code path is waiting.
    await _start_sync_in_background(workspace_id)


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
    c = await _client_for_workspace(workspace_id)
    if c is None:
        log.warning("roster_channel_skipped_no_token", workspace_id=str(workspace_id), channel=slack_channel_id)
        return
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
    Returns 0/1/many.

    On miss: join (or kick off) the background sync with a 10s bounded wait,
    then retry. The bound prevents a single find_user call from blocking the
    chat_postMessage path indefinitely when a workspace's users.list is slow."""
    matches = await _find_user_query(workspace_id, query)
    if matches:
        return matches
    evt = await _start_sync_in_background(workspace_id)
    try:
        await asyncio.wait_for(evt.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        log.info("find_user_sync_timeout", workspace_id=str(workspace_id), query=query)
    return await _find_user_query(workspace_id, query)


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
