"""List Composio connected accounts for a given user_id.

This is the script that would have caught the singular vs plural query
param bug on 2026-05-29 in one run instead of 5 deploys.

Usage:
    doppler run -- uv run python scripts/composio_list_connections.py <user_id> [toolkit_slug]

Example:
    doppler run -- uv run python scripts/composio_list_connections.py \\
        8192aaf0-e38c-4385-9a3f-4e651c984b75 metabase
"""

from __future__ import annotations

import asyncio
import json
import sys

from app.integrations import composio as cz


async def main() -> int:
    if len(sys.argv) < 2:
        print("usage: composio_list_connections.py <user_id> [toolkit_slug]", file=sys.stderr)
        return 2
    user_id = sys.argv[1]
    toolkit_slug = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"→ Composio.list_connections(user_id={user_id!r}, toolkit_slug={toolkit_slug!r})")
    try:
        connections = await cz.list_connections(user_id=user_id, toolkit_slug=toolkit_slug)
    except cz.ComposioHTTPError as e:
        print(f"ERROR status={e.status} body={e.body[:300]}", file=sys.stderr)
        return 1

    print(f"\n← {len(connections)} connections returned\n")
    for c in connections:
        print(json.dumps(c, indent=2, default=str))
        print("-" * 40)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
