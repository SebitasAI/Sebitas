"""Per-workspace bot-token lookup with in-process cache.

The cache is intentionally simple (a dict guarded by an asyncio.Lock) because
at <100 workspaces the DB hit cost is irrelevant, and at 100+ we should be
running multiple workers behind a load balancer and the cache becomes
per-worker (which is fine).

`current_bot_token()` reads the workspace_id from the run contextvar so any
async code path inside a run can ask for the right token without threading
it through call sites.
"""

from __future__ import annotations

import asyncio
import uuid

import structlog
from sqlalchemy import select

from app.agent.context import workspace_id_var
from app.db.models import Workspace
from app.db.session import get_session
from app.slack.crypto import TokenCryptoError, decrypt_token

log = structlog.get_logger(__name__)


_cache: dict[str, tuple[str, str | None]] = {}  # team_id -> (bot_token_plaintext, bot_user_id)
_cache_by_ws: dict[uuid.UUID, tuple[str, str | None]] = {}  # workspace_id -> same
_cache_lock = asyncio.Lock()


def _bust_team(team_id: str) -> None:
    _cache.pop(team_id, None)


def _bust_workspace(ws_id: uuid.UUID) -> None:
    _cache_by_ws.pop(ws_id, None)


def invalidate_token_cache() -> None:
    """Drop all cached tokens. Call after an install / uninstall mutation."""
    _cache.clear()
    _cache_by_ws.clear()


async def get_bot_token_by_team(team_id: str) -> tuple[str, str | None] | None:
    """Returns (decrypted_xoxb, bot_user_id) for the workspace identified by
    its Slack team_id, or None if the workspace isn't installed."""
    async with _cache_lock:
        cached = _cache.get(team_id)
    if cached is not None:
        return cached

    async with get_session() as session:
        row = (
            await session.execute(
                select(Workspace).where(Workspace.slack_team_id == team_id)
            )
        ).scalar_one_or_none()
    if row is None or not row.bot_token:
        return None
    try:
        plain = decrypt_token(row.bot_token)
    except TokenCryptoError as exc:
        log.warning("bot_token_decrypt_failed", team_id=team_id, error=str(exc))
        return None
    pair = (plain, row.bot_user_id)
    async with _cache_lock:
        _cache[team_id] = pair
    return pair


async def get_bot_token_by_workspace(workspace_id: uuid.UUID) -> tuple[str, str | None] | None:
    async with _cache_lock:
        cached = _cache_by_ws.get(workspace_id)
    if cached is not None:
        return cached
    async with get_session() as session:
        row = (
            await session.execute(select(Workspace).where(Workspace.id == workspace_id))
        ).scalar_one_or_none()
    if row is None or not row.bot_token:
        return None
    try:
        plain = decrypt_token(row.bot_token)
    except TokenCryptoError as exc:
        log.warning("bot_token_decrypt_failed", workspace_id=str(workspace_id), error=str(exc))
        return None
    pair = (plain, row.bot_user_id)
    async with _cache_lock:
        _cache_by_ws[workspace_id] = pair
    return pair


async def current_bot_token() -> str | None:
    """Reads workspace_id from the run contextvar and resolves its bot_token.
    Returns None if we're not inside a workspace-scoped run."""
    ws_str = workspace_id_var.get()
    if not ws_str:
        return None
    pair = await get_bot_token_by_workspace(uuid.UUID(ws_str))
    return pair[0] if pair else None
