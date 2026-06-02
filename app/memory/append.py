"""Append a single observation to a memory skill's body.

Body layout (see `seed.py` for the bootstrap template):

    <!-- bootstrapped: ... -->
    ## Curated summary
    ...curated text...

    ## Observations log
    - 2026-06-02T14:23Z [explicit-remember]: Sam uses Folk CRM
    - 2026-06-02T18:45Z [post-pass]: Antiff es plataforma de chargebacks

Append-only is intentional: Phase A never rewrites the curated section.
Phase C (compaction loop) will eventually consolidate observations into
the curated summary on a periodic schedule. Until then the body grows;
we cap individual observation length so it doesn't grow uncontrollably.

Concurrency: optimistic locking via skill.version. Two concurrent appends
to the same skill race -- the loser refetches and retries. We retry up
to MAX_APPEND_RETRIES; after that, we give up and log. The caller is
expected to schedule this as a fire-and-forget task, so a dropped
observation is annoying but not user-visible.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

import structlog
from sqlalchemy import select

from app.db.models import Skill
from app.db.session import get_session
from app.skills import registry as skill_registry
from app.skills import storage as skill_storage

log = structlog.get_logger(__name__)

# How long a single observation is allowed to be (chars). Prevents the
# agent from appending a paragraph -- forces it to summarize.
MAX_OBSERVATION_CHARS: int = 500

# Cap on total body bytes. The R2 layer already enforces 256KB; we hard-
# fail earlier to surface "this skill needs compaction" as a structlog
# event rather than letting R2 reject the upload.
MAX_BODY_BYTES: int = 200_000

# Optimistic-lock retry budget. Two concurrent appends to the same skill
# is rare; three is suspicious; more than that means something is wrong.
MAX_APPEND_RETRIES: int = 3


_LOG_HEADER = "## Observations log"


SourceTag = Literal["explicit-remember", "post-pass"]


def _format_observation(text: str, source: SourceTag, when: datetime) -> str:
    """One markdown bullet, fixed shape so the compaction parser is trivial."""
    ts = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    safe = text.strip().replace("\n", " ")
    if len(safe) > MAX_OBSERVATION_CHARS:
        safe = safe[:MAX_OBSERVATION_CHARS].rstrip() + "…"
    return f"- {ts} [{source}]: {safe}"


def _append_to_body(body: str, observation_line: str) -> str:
    """Insert the observation line at the END of the body. If the
    `## Observations log` header is missing (corrupt body or hand-edit
    by an admin), we add it before appending so subsequent reads still
    parse cleanly."""
    if _LOG_HEADER not in body:
        # Ensure a blank line separates the new section from whatever
        # precedes it; trailing newline keeps subsequent appends tidy.
        if not body.endswith("\n"):
            body += "\n"
        body += f"\n{_LOG_HEADER}\n"
    if not body.endswith("\n"):
        body += "\n"
    return body + observation_line + "\n"


async def append_observation(
    workspace_id: uuid.UUID,
    skill_name: str,
    *,
    text: str,
    source: SourceTag,
    when: datetime | None = None,
) -> bool:
    """Append a single observation to the memory skill identified by
    `(workspace_id, skill_name)`. Returns True on success, False on any
    silent failure (skill missing, body cap exceeded, lock contention
    exhausted, R2 error, etc.).

    Never raises. Callers can fire-and-forget this from the agent loop.
    """
    if not text or not text.strip():
        return False
    when = when or datetime.now(timezone.utc)
    line = _format_observation(text, source, when)

    for attempt in range(1, MAX_APPEND_RETRIES + 1):
        async with get_session() as session:
            skill = (
                await session.execute(
                    select(Skill).where(
                        Skill.workspace_id == workspace_id,
                        Skill.name == skill_name,
                    )
                )
            ).scalar_one_or_none()
        if skill is None:
            log.warning(
                "memory_append_skill_missing",
                workspace_id=str(workspace_id),
                skill_name=skill_name,
            )
            return False

        # Load current body. We do this OUTSIDE the session for the read
        # (R2 download) to keep the optimistic-lock window short; we
        # re-fetch the version inside the write call below.
        try:
            current_body = await skill_storage.download_skill_body(
                workspace_id=skill.workspace_id,
                skill_id=skill.id,
                version=skill.version,
                r2_ref=skill.body_r2_ref,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "memory_append_download_failed",
                skill_name=skill_name,
                error=str(exc)[:200],
            )
            return False

        new_body = _append_to_body(current_body, line)
        new_size = len(new_body.encode("utf-8"))
        if new_size > MAX_BODY_BYTES:
            log.warning(
                "memory_append_body_capped",
                skill_name=skill_name,
                current_size=len(current_body.encode("utf-8")),
                new_size=new_size,
                cap=MAX_BODY_BYTES,
                note="needs compaction (Phase C)",
            )
            return False

        # Optimistic locking: re-fetch the row in a fresh transaction
        # and compare version. If unchanged, write; otherwise loop.
        async with get_session() as session:
            current = await session.get(Skill, skill.id)
            if current is None:
                # Deleted between read and write. Treat as missing.
                return False
            if current.version != skill.version:
                log.info(
                    "memory_append_conflict_retry",
                    skill_name=skill_name,
                    attempt=attempt,
                    seen_version=skill.version,
                    current_version=current.version,
                )
                continue
            # Apply -- delegate to registry helper so version bump + R2
            # upload + LRU invalidation are consistent with the rest of
            # the codebase.
            try:
                await skill_registry.update_skill_body(
                    skill_id=current.id,
                    new_body=new_body,
                    new_size_bytes=new_size,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "memory_append_failed",
                    skill_name=skill_name,
                    error=str(exc)[:200],
                )
                return False
            log.info(
                "memory_observation_appended",
                workspace_id=str(workspace_id),
                skill_name=skill_name,
                source=source,
                content_len=len(text),
            )
            return True

    log.warning(
        "memory_append_retries_exhausted",
        skill_name=skill_name,
        attempts=MAX_APPEND_RETRIES,
    )
    return False


__all__ = ["append_observation", "MAX_OBSERVATION_CHARS", "MAX_BODY_BYTES"]
