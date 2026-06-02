"""Persistent workspace memory (slice T-X Phase A).

Three "memory skills" per workspace, distinguished by reserved slug:
  - `company`           workspace-scope, generic company info
  - `team`              workspace-scope, who-is-who + channels
  - `users/<slack_id>`  personal-scope (owner=that user), per-user memory

The agent reads them via the auto-load policy in prompt_builder (always
injected into the calling user's context) and writes them via the
`remember` agent tool. Bodies are append-only logs; compaction is Phase C.

Phase A intentionally does NOT include:
  - autonomous fact extraction (haiku post-pass) -> Phase B
  - periodic compaction loop -> Phase C
  - onboarding scan of channel history -> Phase D
"""

from app.memory.constants import (
    COMPANY_SLUG,
    TEAM_SLUG,
    USER_SLUG_PREFIX,
    is_memory_skill_name,
    user_slug,
)

__all__ = [
    "COMPANY_SLUG",
    "TEAM_SLUG",
    "USER_SLUG_PREFIX",
    "is_memory_skill_name",
    "user_slug",
]
