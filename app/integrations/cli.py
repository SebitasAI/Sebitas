"""Integrations CLI: connect a test account (via Pipedream connect link), then
sync the connected accounts into integration_connection. No UI; the OAuth step
happens in a browser.

Run under Doppler, e.g.:
  doppler run -- uv run python -m app.integrations.cli connect gitlab --team T0...
  doppler run -- uv run python -m app.integrations.cli sync --team T0...
  doppler run -- uv run python -m app.integrations.cli list --team T0...
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.db.models import IntegrationConnection, Workspace
from app.db.session import get_session
from app.integrations.pipedream_provider import get_provider


async def _resolve_workspace(team_id: str):
    async with get_session() as session:
        ws = (
            await session.execute(select(Workspace).where(Workspace.slack_team_id == team_id))
        ).scalar_one_or_none()
    if ws is None:
        print(f"No existe workspace para team {team_id!r}. Escribile al bot primero.")
        sys.exit(1)
    return ws.id


async def do_connect(app: str, team: str) -> None:
    ws = await _resolve_workspace(team)
    data = await get_provider().create_connect_link(str(ws))
    url = data.get("connect_link_url")
    if url and app:
        url = url + ("&" if "?" in url else "?") + f"app={app}"
    print("Abrí este link en el browser y conectá la cuenta:")
    print("  ", url or data)
    print(f"Cuando termines: doppler run -- uv run python -m app.integrations.cli sync --team {team}")


async def do_sync(team: str) -> None:
    ws = await _resolve_workspace(team)
    accounts = await get_provider().list_accounts(str(ws))
    count = 0
    async with get_session() as session:
        for acc in accounts:
            app_obj = acc.get("app") or {}
            app = app_obj.get("name_slug") or app_obj.get("name") or (acc.get("app") if isinstance(acc.get("app"), str) else None)
            acc_id = acc.get("id")
            if not app or not acc_id:
                continue
            existing = (
                await session.execute(
                    select(IntegrationConnection).where(
                        IntegrationConnection.workspace_id == ws, IntegrationConnection.app == app
                    )
                )
            ).scalar_one_or_none()
            if existing:
                existing.pipedream_account_id = acc_id
                existing.status = "connected"
            else:
                session.add(IntegrationConnection(workspace_id=ws, app=app, pipedream_account_id=acc_id, status="connected"))
            count += 1
        await session.commit()
    print(f"Sincronizadas {count} cuenta(s) para workspace {ws}")


async def do_list(team: str) -> None:
    ws = await _resolve_workspace(team)
    async with get_session() as session:
        rows = (
            await session.execute(
                select(IntegrationConnection).where(IntegrationConnection.workspace_id == ws)
            )
        ).scalars().all()
    if not rows:
        print("(ninguna integración conectada)")
    for r in rows:
        print(f"- {r.app} [{r.status}] account={r.pipedream_account_id}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.integrations.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_c = sub.add_parser("connect")
    p_c.add_argument("app")
    p_c.add_argument("--team", required=True)
    p_s = sub.add_parser("sync")
    p_s.add_argument("--team", required=True)
    p_l = sub.add_parser("list")
    p_l.add_argument("--team", required=True)
    args = parser.parse_args()

    if args.cmd == "connect":
        asyncio.run(do_connect(args.app, args.team))
    elif args.cmd == "sync":
        asyncio.run(do_sync(args.team))
    elif args.cmd == "list":
        asyncio.run(do_list(args.team))


if __name__ == "__main__":
    main()
