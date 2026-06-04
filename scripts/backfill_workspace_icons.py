"""One-shot backfill: pull Slack team icons for every installed workspace.

After migration 0033 adds `workspace.slack_team_icon_url`, fresh
installs populate the column via `install_store._fetch_team_icon`.
This script handles **existing** workspaces (Simetrik, Antiff, diio,
Supersonik, etc) that won't re-install and would otherwise stay NULL
forever -- which means the web sidebar keeps showing the initial-letter
fallback.

For each workspace with `bot_token IS NOT NULL` and
`slack_team_icon_url IS NULL`, decrypt the token, call `team.info`,
and persist the largest icon URL. Skips workspaces that have no icon
(image_default) or where the API call fails -- next run picks them up.

Usage:
  doppler run -p sebitas -c dev -- uv run python scripts/backfill_workspace_icons.py
  doppler run -p sebitas -c prd -- uv run python scripts/backfill_workspace_icons.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.models import Workspace  # noqa: E402
from app.db.session import get_session  # noqa: E402
from app.slack.crypto import TokenCryptoError, decrypt_token  # noqa: E402
from app.slack.install_store import _fetch_team_icon  # noqa: E402


async def main() -> int:
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Workspace).where(
                    Workspace.bot_token.is_not(None),
                    Workspace.slack_team_icon_url.is_(None),
                )
            )
        ).scalars().all()

    if not rows:
        print("No workspaces need a backfill.")
        return 0

    print(f"Backfilling {len(rows)} workspace icons...")
    updated = 0
    skipped = 0
    for ws in rows:
        try:
            plain = decrypt_token(ws.bot_token)
        except TokenCryptoError as exc:
            print(f"  [SKIP] {ws.name or ws.slack_team_id}: token decrypt failed: {exc}")
            skipped += 1
            continue
        icon = await _fetch_team_icon(plain)
        if not icon:
            print(f"  [SKIP] {ws.name or ws.slack_team_id}: no icon (image_default or fetch failed)")
            skipped += 1
            continue
        async with get_session() as session:
            ws_live = await session.get(Workspace, ws.id)
            if ws_live is None:
                continue
            ws_live.slack_team_icon_url = icon
            await session.commit()
        print(f"  [OK]   {ws.name or ws.slack_team_id} -> {icon}")
        updated += 1

    print(f"Done. updated={updated}, skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
