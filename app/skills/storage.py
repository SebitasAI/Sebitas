"""R2-backed storage for skill bodies plus an in-process LRU cache.

Layout:

    skills/{workspace_id}/{skill_id}.md

The R2 key intentionally has no version suffix in v1: an update overwrites the
same key. We bump `skill.version` in Postgres on every body change so the LRU
cache (keyed by `(workspace_id, skill_id, version)`) sees a miss and re-pops
from R2. When we eventually ship UI for previous versions, we migrate to
versioned keys + async GC; the rest of the stack doesn't need to change.

Size enforcement is here too: anything above `MAX_BODY_BYTES` is rejected
before touching R2.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict

import structlog

from app.artifacts import r2

log = structlog.get_logger(__name__)

# Hard ceiling. Anything bigger gets rejected at the Slack handler before the
# bytes are read. 256 KB is plenty for any reasonable behavioral / knowledge-
# base markdown (orders of magnitude above a SKILL.md).
MAX_BODY_BYTES = 256 * 1024

# In-process body cache. Keyed by (workspace_id, skill_id, version). Bounded
# by entry count, not byte size, since each body is < 256 KB anyway. 50 entries
# at ~256 KB each is 12.8 MB worst case; in practice much less.
_CACHE_MAX_ENTRIES = 50


class SkillBodyTooLarge(ValueError):
    """Raised when a body would exceed MAX_BODY_BYTES. Caught upstream so the
    user sees a friendly Slack error, not a stack trace."""


def _r2_key(workspace_id: uuid.UUID, skill_id: uuid.UUID) -> str:
    return f"skills/{workspace_id}/{skill_id}.md"


# Module-level LRU. Process-local; on Render rolling deploys the cache resets,
# which is fine: a miss costs one R2 GET.
_body_cache: "OrderedDict[tuple[str, str, int], str]" = OrderedDict()


def _cache_get(key: tuple[str, str, int]) -> str | None:
    val = _body_cache.get(key)
    if val is not None:
        _body_cache.move_to_end(key)
    return val


def _cache_put(key: tuple[str, str, int], value: str) -> None:
    _body_cache[key] = value
    _body_cache.move_to_end(key)
    while len(_body_cache) > _CACHE_MAX_ENTRIES:
        _body_cache.popitem(last=False)


def _cache_invalidate_skill(workspace_id: uuid.UUID, skill_id: uuid.UUID) -> None:
    """Drop every cache entry for a skill across all versions. Called on
    delete; on update the new version is a natural miss but we still purge the
    old entries to free space."""
    ws_s, sk_s = str(workspace_id), str(skill_id)
    stale = [k for k in _body_cache if k[0] == ws_s and k[1] == sk_s]
    for k in stale:
        _body_cache.pop(k, None)


async def upload_skill_body(
    workspace_id: uuid.UUID,
    skill_id: uuid.UUID,
    version: int,
    content: str,
) -> str:
    """Write the skill body to R2. Returns the R2 key (the value to store in
    `skill.body_r2_ref`). Caller is responsible for the Postgres row."""
    data = content.encode("utf-8")
    if len(data) > MAX_BODY_BYTES:
        raise SkillBodyTooLarge(
            f"skill body is {len(data)} bytes, max is {MAX_BODY_BYTES}"
        )
    key = _r2_key(workspace_id, skill_id)
    await r2.put_bytes(key, data, content_type="text/markdown; charset=utf-8")
    # Pre-populate the cache so the first read after upload doesn't pay an R2
    # round-trip.
    _cache_put((str(workspace_id), str(skill_id), version), content)
    log.info(
        "skill_body_uploaded",
        workspace_id=str(workspace_id),
        skill_id=str(skill_id),
        version=version,
        size_bytes=len(data),
    )
    return key


async def download_skill_body(
    workspace_id: uuid.UUID,
    skill_id: uuid.UUID,
    version: int,
    r2_ref: str,
) -> str:
    """LRU-cached body fetch. Cache key includes version so a bump on update
    naturally misses and re-pops the latest content."""
    ck = (str(workspace_id), str(skill_id), version)
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    body = await r2.get_text(r2_ref)
    _cache_put(ck, body)
    return body


async def delete_skill_body(
    workspace_id: uuid.UUID, skill_id: uuid.UUID, r2_ref: str
) -> None:
    """Remove the body from R2 and purge any cached copies. Idempotent:
    swallows R2 errors so the caller (which is usually in the middle of a
    DELETE workflow) doesn't fail because the object was already gone."""
    _cache_invalidate_skill(workspace_id, skill_id)
    try:
        # r2 module doesn't expose delete yet; we go through the boto client
        # directly. Failure to delete a body is logged but not raised: a
        # leaked object is a janitor problem, not a user problem.
        import asyncio

        from app.artifacts.r2 import _client  # noqa: PLC2701
        from app.config import get_settings

        s = get_settings()

        def _do() -> None:
            _client().delete_object(Bucket=s.r2_bucket, Key=r2_ref)

        await asyncio.to_thread(_do)
        log.info(
            "skill_body_deleted",
            workspace_id=str(workspace_id),
            skill_id=str(skill_id),
            r2_ref=r2_ref,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "skill_body_delete_failed",
            workspace_id=str(workspace_id),
            skill_id=str(skill_id),
            r2_ref=r2_ref,
            error=str(exc)[:200],
        )


def cache_stats() -> dict:
    """For observability + tests. Cheap; safe to call from any context."""
    return {"size": len(_body_cache), "max": _CACHE_MAX_ENTRIES}
