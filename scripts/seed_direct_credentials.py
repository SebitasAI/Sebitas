"""One-shot: seed direct credentials for a workspace+app into the DB.

Replaces the env-var-based fallback (METABASE_FALLBACK_*). The DB column
scales to N tenants without touching env vars. After running this for every
existing live tenant, the env vars can be safely removed from Doppler.

Usage:
    doppler run -- uv run python scripts/seed_direct_credentials.py \\
        <team_id|workspace_id> <app> --key <api_key> --base-url <url>

The first positional is either:
- A Slack team_id (TXXXXXXXX) — looked up to its workspace.id
- A workspace.id UUID — used directly

Examples:

    # Antiff (workspace UUID known)
    doppler run -- uv run python scripts/seed_direct_credentials.py \\
        8192aaf0-e38c-4385-9a3f-4e651c984b75 metabase \\
        --key 'mb_AdGo...' --base-url 'https://simetrik.metabaseapp.com'

    # Simetrik via team_id
    doppler run -- uv run python scripts/seed_direct_credentials.py \\
        TH3PWEDA7 metabase \\
        --key 'mb_AdGo...' --base-url 'https://simetrik.metabaseapp.com'
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from sqlalchemy import select

from app.db.models import Workspace
from app.db.session import get_session
from app.integrations.direct_credentials import (
    get_direct_credentials,
    set_direct_credentials,
)


async def _resolve_workspace_id(arg: str) -> uuid.UUID | None:
    """Accept either a Slack team_id or a workspace.id UUID; return the
    workspace.id either way, or None if not found."""
    try:
        return uuid.UUID(arg)
    except (ValueError, TypeError):
        pass
    async with get_session() as session:
        ws = (await session.execute(
            select(Workspace).where(Workspace.slack_team_id == arg)
        )).scalar_one_or_none()
    return ws.id if ws else None


async def main() -> int:
    ap = argparse.ArgumentParser(description="Seed direct credentials for a tenant+app.")
    ap.add_argument("workspace", help="Slack team_id (TXXX) or workspace.id UUID")
    ap.add_argument("app", help="App slug (e.g. 'metabase')")
    ap.add_argument("--key", required=True, help="API key (mb_..., sk_..., etc.)")
    ap.add_argument(
        "--base-url", required=False, default=None,
        help="Base URL of the upstream app (e.g. https://x.metabaseapp.com)",
    )
    args = ap.parse_args()

    workspace_id = await _resolve_workspace_id(args.workspace)
    if workspace_id is None:
        print(f"No workspace found for {args.workspace!r}", file=sys.stderr)
        return 1

    creds: dict = {"api_key": args.key}
    if args.base_url:
        creds["base_url"] = args.base_url

    # Idempotency-friendly: if creds already match, skip (avoid bumping
    # ciphertext on every run; same plaintext gives a different Fernet
    # ciphertext each time because of the IV).
    existing = await get_direct_credentials(workspace_id, args.app)
    if existing == creds:
        print(f"Credentials already match for workspace={workspace_id} app={args.app}; no change.")
        return 0

    await set_direct_credentials(workspace_id, args.app, creds)
    print(f"Stored credentials for workspace={workspace_id} app={args.app} keys={sorted(creds.keys())}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
