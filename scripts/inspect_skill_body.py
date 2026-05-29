"""Download a skill body from R2 and print it.

Built specifically to investigate the metabase-catalog-simetrik /
misterr-bi-agent-training-simetrik / simetrik-datalake-context skills
flagged on 2026-05-29 as potentially containing a plaintext admin API key.

Usage:
    doppler run -- uv run python scripts/inspect_skill_body.py <skill_id_or_name> [workspace_id]

If you pass a UUID, it's treated as skill.id; otherwise as skill.name. The
workspace_id is required for name-based lookup (skills are tenant-scoped).

Example:
    doppler run -- uv run python scripts/inspect_skill_body.py metabase-catalog-simetrik \\
        8192aaf0-e38c-4385-9a3f-4e651c984b75
"""

from __future__ import annotations

import asyncio
import re
import sys
import uuid

from sqlalchemy import select

from app.artifacts.r2 import get_text
from app.db.models import Skill
from app.db.session import get_session


# Patterns that are dead giveaways for leaked credentials. We grep the body
# for these and print a warning at the bottom of the output, separate from
# the body dump.
_SECRET_PATTERNS = [
    (re.compile(r"mb_[A-Za-z0-9_-]{20,}"), "Metabase API key (mb_*)"),
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "Generic secret (sk-*)"),
    (re.compile(r"api[_-]?key[\"'\s:=]+[A-Za-z0-9_-]{16,}", re.I), "api_key= literal"),
    (re.compile(r"bearer\s+[A-Za-z0-9_.-]{20,}", re.I), "bearer token"),
    (re.compile(r"password[\"'\s:=]+[^\s\"']{6,}", re.I), "password= literal"),
]


async def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: inspect_skill_body.py <skill_id_or_name> [workspace_id]",
            file=sys.stderr,
        )
        return 2
    arg = sys.argv[1]
    workspace_id_arg = sys.argv[2] if len(sys.argv) > 2 else None

    async with get_session() as session:
        try:
            skill_id = uuid.UUID(arg)
            row = (await session.execute(
                select(Skill).where(Skill.id == skill_id)
            )).scalar_one_or_none()
        except ValueError:
            if not workspace_id_arg:
                print(
                    "name-based lookup requires workspace_id as 2nd arg",
                    file=sys.stderr,
                )
                return 2
            workspace_id = uuid.UUID(workspace_id_arg)
            row = (await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace_id,
                    Skill.name == arg,
                )
            )).scalar_one_or_none()

    if row is None:
        print(f"No skill found for {arg!r}")
        return 1

    print(f"=== Skill ===")
    print(f"id:           {row.id}")
    print(f"workspace_id: {row.workspace_id}")
    print(f"name:         {row.name}")
    print(f"description:  {row.description}")
    print(f"r2_ref:       {row.body_r2_ref}")
    print(f"source:       {row.source}")
    print(f"=== Body ===\n")
    body = await get_text(row.body_r2_ref)
    print(body)

    # Secret scan.
    hits: list[str] = []
    for pat, label in _SECRET_PATTERNS:
        for m in pat.finditer(body):
            # Truncate the match for the log; don't print the secret itself.
            matched = m.group(0)
            preview = matched[:8] + "..." + matched[-4:] if len(matched) > 16 else "<short>"
            hits.append(f"  • {label}: {preview} (position {m.start()})")
    if hits:
        print("\n=== ⚠️  Potential secrets in skill body ===")
        for h in hits:
            print(h)
        print("\nRotate these credentials and remove them from the skill.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
