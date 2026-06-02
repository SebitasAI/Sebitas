"""One-time backfill: run the onboarding scan for every installed workspace.

Memory skills (Phase A) only got seeded as empty stubs at install time. For
workspaces installed BEFORE the memory slice landed, `company` + `team`
still say "(no information yet about this company)". For workspaces
installed AFTER, the stubs exist but no real content has been written
because neither the user has run `aprende del workspace` nor have they
talked enough to accumulate post-pass facts.

This script walks every installed workspace (those with `bot_token IS NOT
NULL`) and runs `run_onboarding_scan` against each, populating both
`company` and `team` with concrete observations.

Idempotency: re-running re-appends to logs. Phase C compaction folds
duplicates. To avoid spamming a workspace's log when this is run multiple
times (e.g. during testing), we skip workspaces where the `team` skill
already has more than `RESCAN_THRESHOLD` onboarding-scan bullets in its
observations log -- the heuristic is "this workspace already got scanned".

Usage:

  doppler run --project sebitas --config prd -- \\
    .venv/bin/python -m scripts.backfill_memory

  # Or for a single workspace by Slack team_id:
  doppler run --project sebitas --config prd -- \\
    .venv/bin/python -m scripts.backfill_memory --team-id T1234567

  # Force re-scan workspaces that already have observations:
  doppler run --project sebitas --config prd -- \\
    .venv/bin/python -m scripts.backfill_memory --force

Per-workspace failures are logged and the script keeps going. Final
summary is printed to stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

import structlog
from sqlalchemy import select

from app.db.models import Skill, Workspace
from app.db.session import get_session
from app.memory.constants import TEAM_SLUG
from app.memory.onboarding import run_onboarding_scan
from app.skills import storage as skill_storage

log = structlog.get_logger(__name__)


# If `team` already has more than this many onboarding-scan bullets,
# we assume the workspace was scanned before and skip it (unless --force).
# Picked above the noise floor of one-or-two manual `recordá` calls and
# below the typical scan output (which writes 30+ channel observations).
RESCAN_THRESHOLD: int = 15


async def _already_scanned(workspace_id: uuid.UUID) -> bool:
    """Heuristic: count `[onboarding-scan]` bullets in the team skill body.
    Returns True if the workspace looks like it's already been scanned."""
    async with get_session() as session:
        skill = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace_id,
                    Skill.name == TEAM_SLUG,
                )
            )
        ).scalar_one_or_none()
    if skill is None:
        return False
    try:
        body = await skill_storage.download_skill_body(
            workspace_id=skill.workspace_id,
            skill_id=skill.id,
            version=skill.version,
            r2_ref=skill.body_r2_ref,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "backfill_skill_body_read_failed",
            workspace_id=str(workspace_id), error=str(exc)[:200],
        )
        return False
    count = body.count("[onboarding-scan]")
    return count > RESCAN_THRESHOLD


async def _list_installed_workspaces(
    *, team_id: str | None
) -> list[tuple[uuid.UUID, str, str | None]]:
    """Return (workspace_id, team_id, name) for every installed workspace
    (i.e. those with a bot_token). Optionally filter to one team_id."""
    async with get_session() as session:
        stmt = select(
            Workspace.id, Workspace.slack_team_id, Workspace.name
        ).where(Workspace.bot_token.isnot(None))
        if team_id:
            stmt = stmt.where(Workspace.slack_team_id == team_id)
        rows = (await session.execute(stmt)).all()
    return [(r[0], r[1], r[2]) for r in rows]


async def backfill(*, team_id: str | None, force: bool) -> dict[str, int]:
    """Walk every installed workspace (or just the one matching team_id),
    run the onboarding scan, return a summary dict. Failures per workspace
    are logged + skipped; the function never raises."""
    workspaces = await _list_installed_workspaces(team_id=team_id)
    if not workspaces:
        print("No installed workspaces found.", file=sys.stderr)
        return {"workspaces": 0, "scanned": 0, "skipped": 0, "failed": 0}

    summary = {
        "workspaces": len(workspaces),
        "scanned": 0,
        "skipped": 0,
        "failed": 0,
    }

    for ws_id, ws_team_id, ws_name in workspaces:
        label = f"{ws_name or '?'} ({ws_team_id})"
        if not force:
            try:
                if await _already_scanned(ws_id):
                    print(f"  - {label}: SKIP (already scanned, use --force to re-run)")
                    summary["skipped"] += 1
                    continue
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "backfill_idempotency_check_failed",
                    workspace_id=str(ws_id), error=str(exc)[:200],
                )

        print(f"  - {label}: scanning...")
        try:
            result = await run_onboarding_scan(ws_id)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "backfill_workspace_failed",
                workspace_id=str(ws_id), error=str(exc)[:500],
            )
            print(f"      FAILED: {exc}")
            summary["failed"] += 1
            continue

        # Per-workspace summary one-liner.
        parts = []
        for k in (
            "channels_written",
            "members_written",
            "integrations_written",
            "facts_written",
        ):
            v = result.get(k, 0)
            if v:
                parts.append(f"{k.replace('_written','')}={v}")
        print(
            f"      OK: {', '.join(parts) if parts else 'no new observations'}"
        )
        summary["scanned"] += 1

    return summary


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--team-id",
        help="Restrict to a single workspace by Slack team_id (e.g. T1234567).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-run on workspaces that look already scanned.",
    )
    return p.parse_args()


async def main() -> int:
    args = _parse_args()
    print("Running memory backfill...")
    if args.team_id:
        print(f"  filter: team_id={args.team_id}")
    if args.force:
        print("  --force: ignoring already-scanned heuristic")
    print()

    summary = await backfill(team_id=args.team_id, force=args.force)

    print()
    print("=== Summary ===")
    print(f"  workspaces : {summary['workspaces']}")
    print(f"  scanned    : {summary['scanned']}")
    print(f"  skipped    : {summary['skipped']}")
    print(f"  failed     : {summary['failed']}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
