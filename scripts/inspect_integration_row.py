"""Print the integration_connection row for a (workspace, app) pair.

Skips having to log into Render → Postgres → run psql by hand. Useful when
diagnosing "the bot says X is not connected but the user swears they
connected it".

Usage:
    doppler run -- uv run python scripts/inspect_integration_row.py <workspace_id> <app>

Example:
    doppler run -- uv run python scripts/inspect_integration_row.py \\
        8192aaf0-e38c-4385-9a3f-4e651c984b75 metabase
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid

from sqlalchemy import select

from app.db.models import IntegrationConnection
from app.db.session import get_session


async def main() -> int:
    if len(sys.argv) < 3:
        print("usage: inspect_integration_row.py <workspace_id> <app>", file=sys.stderr)
        return 2
    workspace_id = uuid.UUID(sys.argv[1])
    app = sys.argv[2]

    async with get_session() as session:
        rows = (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.app == app,
                )
            )
        ).scalars().all()

    if not rows:
        print(f"No rows for workspace={workspace_id} app={app}")
        return 0

    for r in rows:
        print(json.dumps({
            "id": str(r.id),
            "workspace_id": str(r.workspace_id),
            "app": r.app,
            "status": r.status,
            "provider": r.provider,
            "pipedream_account_id": r.pipedream_account_id,
            "pending_run_id": r.pending_run_id,
            "pending_ctx_keys": list(r.pending_ctx.keys()) if r.pending_ctx else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }, indent=2))
        print("-" * 40)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
