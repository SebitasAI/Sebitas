"""Edit a skill body in R2 + log the change.

Usage:
    # Dry-run: preview the diff without writing
    doppler run -- uv run python scripts/edit_skill_body.py <skill_name> <workspace_id> \\
        --find "OLD_TEXT" --replace "NEW_TEXT" --dry-run

    # Apply the edit (writes the new body to R2, leaves the DB row unchanged)
    doppler run -- uv run python scripts/edit_skill_body.py <skill_name> <workspace_id> \\
        --find "OLD_TEXT" --replace "NEW_TEXT"

    # Delete a line by matching its full text (replace with empty)
    doppler run -- uv run python scripts/edit_skill_body.py ... --find "LINE_TEXT" --replace ""

    # Read a regex pattern instead of literal string
    doppler run -- uv run python scripts/edit_skill_body.py ... --find "regex" --replace "..." --regex

The script downloads the existing body, applies the substitution, shows a
diff, and only writes back if --dry-run is absent. The body is stored at
the same R2 key the skill row points at, so future skill loads pick up
the new content immediately.

Built specifically to clean up the bi-agent-way-of-work-simetrik skill
on 2026-05-29: it had outdated "❌ Operativo / Escalar a RevOps" lines
for dashboard-creation capabilities the bot now actually has via the
Composio integration, causing the bot to refuse tasks it could handle.

Does NOT scan for credentials (use inspect_skill_body.py for that). This
is a surgical text editor; the caller decides what to change."""

from __future__ import annotations

import argparse
import asyncio
import difflib
import re
import sys
import uuid

from sqlalchemy import select

from app.artifacts.r2 import get_text, put_bytes
from app.db.models import Skill
from app.db.session import get_session


async def _load_skill(arg: str, workspace_id_arg: str | None) -> Skill | None:
    async with get_session() as session:
        try:
            sid = uuid.UUID(arg)
            return (await session.execute(
                select(Skill).where(Skill.id == sid)
            )).scalar_one_or_none()
        except ValueError:
            if not workspace_id_arg:
                print("name-based lookup requires workspace_id as 2nd arg", file=sys.stderr)
                return None
            wid = uuid.UUID(workspace_id_arg)
            return (await session.execute(
                select(Skill).where(
                    Skill.workspace_id == wid,
                    Skill.name == arg,
                )
            )).scalar_one_or_none()


def _print_diff(old: str, new: str, label: str) -> None:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"{label} (current)",
        tofile=f"{label} (proposed)",
        n=2,
    )
    sys.stdout.writelines(diff)


async def main() -> int:
    ap = argparse.ArgumentParser(description="Edit a skill body in R2.")
    ap.add_argument("skill", help="skill_id (UUID) or name")
    ap.add_argument(
        "workspace_id", nargs="?",
        help="workspace_id (required when skill is given by name)",
    )
    ap.add_argument(
        "--find", required=True,
        help="literal text to find (or regex pattern if --regex)",
    )
    ap.add_argument(
        "--replace", required=True,
        help="replacement text (use '' to delete the match)",
    )
    ap.add_argument(
        "--regex", action="store_true",
        help="treat --find as a regex pattern instead of literal text",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="show the diff but don't write back to R2",
    )
    args = ap.parse_args()

    row = await _load_skill(args.skill, args.workspace_id)
    if row is None:
        print(f"No skill found for {args.skill!r}", file=sys.stderr)
        return 1

    old_body = await get_text(row.body_r2_ref)

    if args.regex:
        new_body, count = re.subn(args.find, args.replace, old_body)
    else:
        count = old_body.count(args.find)
        new_body = old_body.replace(args.find, args.replace)

    if count == 0:
        print(f"No matches found for {args.find!r}. Nothing to do.")
        return 0
    if new_body == old_body:
        print("Replacement produced identical body. Nothing to do.")
        return 0

    print(f"=== {row.name} ({row.id}) — {count} occurrence(s) ===\n")
    _print_diff(old_body, new_body, row.name)
    print()

    if args.dry_run:
        print(f"(dry-run, not writing) Would write {len(new_body)} bytes to {row.body_r2_ref}")
        return 0

    # Confirm before writing.
    confirm = input(f"Apply this edit to R2 key {row.body_r2_ref}? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return 1

    await put_bytes(
        row.body_r2_ref,
        new_body.encode("utf-8"),
        content_type="text/markdown; charset=utf-8",
    )
    print(f"Wrote {len(new_body)} bytes to {row.body_r2_ref}")
    print("The next time the skill loads (per-thread, on demand), the new body is served.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
