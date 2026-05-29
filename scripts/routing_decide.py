"""Print which provider routing would pick for a given app.

No DB writes. Useful when asking "is metabase routed to Composio or Pipedream?"
and you don't want to wait for a Slack round-trip to find out.

Usage:
    doppler run -- uv run python scripts/routing_decide.py <app> [workspace_id]

If workspace_id is omitted, only the fresh-decision path is exercised (no
existing-row lookup). If provided, the script also reports what the existing
row says, so you can spot drift between "what we'd pick" and "what we have".
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from app.integrations.routing import (
    decide_provider_for_new_connection,
    provider_for_existing_connection,
)


async def main() -> int:
    if len(sys.argv) < 2:
        print("usage: routing_decide.py <app> [workspace_id]", file=sys.stderr)
        return 2
    app = sys.argv[1]

    fresh = await decide_provider_for_new_connection(app)
    print(f"fresh decision for {app!r}: {fresh}")

    if len(sys.argv) > 2:
        workspace_id = uuid.UUID(sys.argv[2])
        existing = await provider_for_existing_connection(workspace_id, app)
        if existing is None:
            print(f"existing row for {app!r}: none")
        else:
            _, name = existing
            print(f"existing row for {app!r}: {name}")
            if name != fresh:
                print(f"  ⚠️  drift: fresh would pick {fresh}, row pinned to {name}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
