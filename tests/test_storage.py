"""Unit tests for app.skills.storage: size limit + LRU cache behaviour.

R2 is replaced with the in-memory fake from conftest, so these tests don't
touch the network."""

from __future__ import annotations

import uuid

import pytest

from app.skills import storage
from app.skills.storage import SkillBodyTooLarge


@pytest.mark.asyncio
async def test_skill_size_limit_rejects_oversized(fake_r2):
    """Bodies above MAX_BODY_BYTES raise SkillBodyTooLarge and never reach R2."""
    workspace_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    too_big = "x" * (storage.MAX_BODY_BYTES + 1)
    with pytest.raises(SkillBodyTooLarge):
        await storage.upload_skill_body(
            workspace_id=workspace_id, skill_id=skill_id, version=1, content=too_big
        )
    assert fake_r2.objects == {}


@pytest.mark.asyncio
async def test_upload_then_download_uses_cache(fake_r2):
    """First read after upload comes from the LRU (pre-populated by upload);
    we verify by deleting the R2 object directly and asserting the next read
    still succeeds (cache hit)."""
    workspace_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    body = "hello skill"
    r2_ref = await storage.upload_skill_body(
        workspace_id=workspace_id, skill_id=skill_id, version=1, content=body
    )
    # Wipe the only R2 object; cache hit must still return the body.
    fake_r2.objects.clear()
    out = await storage.download_skill_body(
        workspace_id=workspace_id, skill_id=skill_id, version=1, r2_ref=r2_ref
    )
    assert out == body


@pytest.mark.asyncio
async def test_version_bump_misses_cache(fake_r2):
    """LRU keys include version, so bumping version forces a re-read from R2."""
    workspace_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    r2_ref = await storage.upload_skill_body(
        workspace_id=workspace_id, skill_id=skill_id, version=1, content="v1"
    )
    # Overwrite the R2 object with new content (simulating update_skill_body
    # at version 2) but the version-1 cache entry should still be present.
    await storage.upload_skill_body(
        workspace_id=workspace_id, skill_id=skill_id, version=2, content="v2"
    )
    # Reading v1 still gives v1 from cache; reading v2 gives v2.
    assert await storage.download_skill_body(
        workspace_id=workspace_id, skill_id=skill_id, version=1, r2_ref=r2_ref
    ) == "v1"
    assert await storage.download_skill_body(
        workspace_id=workspace_id, skill_id=skill_id, version=2, r2_ref=r2_ref
    ) == "v2"


@pytest.mark.asyncio
async def test_delete_invalidates_cache(fake_r2):
    """delete_skill_body purges every version of the skill from the cache."""
    workspace_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    r2_ref = await storage.upload_skill_body(
        workspace_id=workspace_id, skill_id=skill_id, version=1, content="x"
    )
    assert storage.cache_stats()["size"] >= 1
    await storage.delete_skill_body(workspace_id, skill_id, r2_ref)
    # No stale entries for this skill.
    assert all(
        k[1] != str(skill_id) for k in storage._body_cache.keys()
    )
