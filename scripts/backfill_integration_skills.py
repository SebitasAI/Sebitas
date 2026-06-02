"""One-time backfill: generate `integrations/<app>` skills for every
connected integration in every installed workspace.

After this script runs, every workspace has a catalog skill per app
they've connected via Pipedream. The agent can then call
`load_skill('integrations/<app>')` to see the full action list +
configurable props before deciding which action to invoke.

Idempotent: re-running just refreshes the auto-generated `## Available
actions` section. The `## Usage notes` section (admin-edited + auto-
improve-appended) is preserved.

Usage:

  doppler run --project sebitas --config prd -- \\
    .venv/bin/python -m scripts.backfill_integration_skills

  # Restrict to one workspace:
  doppler run --project sebitas --config prd -- \\
    .venv/bin/python -m scripts.backfill_integration_skills --team-id T1234567

The Pipedream catalog cache lives at the app level (not workspace), so
backfilling 4 workspaces × 7 apps doesn't hit Pipedream 28 times --
~10 unique apps × 1 catalog fetch each.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

import structlog
from sqlalchemy import select

from app.db.models import Workspace
from app.db.session import get_session
from app.integrations.catalog_skills import refresh_all_for_workspace

log = structlog.get_logger(__name__)


async def _resolve_workspace(team_id: str) -> uuid.UUID | None:
    async with get_session() as session:
        row = (
            await session.execute(
                select(Workspace.id).where(Workspace.slack_team_id == team_id)
            )
        ).scalars().first()
    return row


async def _list_installed_workspaces() -> list[tuple[uuid.UUID, str, str | None]]:
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Workspace.id, Workspace.slack_team_id, Workspace.name)
                .where(Workspace.bot_token.isnot(None))
            )
        ).all()
    return [(r[0], r[1], r[2]) for r in rows]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--team-id",
        help="Restrict to a single workspace by Slack team_id (e.g. T1234567).",
    )
    return p.parse_args()


async def main() -> int:
    args = _parse_args()

    if args.team_id:
        wid = await _resolve_workspace(args.team_id)
        if wid is None:
            print(f"No workspace found for team_id={args.team_id}", file=sys.stderr)
            return 1
        workspaces = [(wid, args.team_id, None)]
    else:
        workspaces = await _list_installed_workspaces()
        if not workspaces:
            print("No installed workspaces found.", file=sys.stderr)
            return 1

    print(f"Backfilling integration skills for {len(workspaces)} workspace(s)...")
    print()

    total = {"workspaces": 0, "apps": 0, "upserted": 0, "failed": 0}
    for ws_id, team_id, name in workspaces:
        label = f"{name or '?'} ({team_id})"
        print(f"  - {label}: refreshing...")
        try:
            counts = await refresh_all_for_workspace(ws_id)
        except Exception as exc:  # noqa: BLE001
            log.error("backfill_workspace_failed", error=str(exc)[:500])
            print(f"      FAILED: {exc}")
            continue
        print(
            f"      OK: apps={counts['apps']} upserted={counts['upserted']} "
            f"failed={counts['failed']}"
        )
        total["workspaces"] += 1
        total["apps"] += counts["apps"]
        total["upserted"] += counts["upserted"]
        total["failed"] += counts["failed"]

    print()
    print("=== Summary ===")
    for k, v in total.items():
        print(f"  {k:13}: {v}")
    return 0 if total["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
