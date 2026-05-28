"""CLI for per-workspace Slack install/list/uninstall + backfill.

Used to onboard pilot workspaces manually until the OAuth install flow lands.
The bot_token is stored Fernet-encrypted in `workspace.bot_token`.

Run under Doppler:
  doppler run -- python -m app.slack.cli install --team T123 --token xoxb-... [--bot-user-id Uxxx]
  doppler run -- python -m app.slack.cli list
  doppler run -- python -m app.slack.cli uninstall --team T123
  doppler run -- python -m app.slack.cli backfill --team T0ATZQRBA5D
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Workspace
from app.db.session import get_session
from app.slack.crypto import encrypt_token
from app.slack.tokens import invalidate_token_cache


async def do_install(team: str, token: str, bot_user_id: str | None, scopes: str | None) -> None:
    if not token.startswith("xoxb-"):
        print(f"warn: token doesn't start with xoxb-; got prefix {token[:8]!r}")
    enc = encrypt_token(token)
    async with get_session() as session:
        ws = (
            await session.execute(select(Workspace).where(Workspace.slack_team_id == team))
        ).scalar_one_or_none()
        if ws is None:
            ws = Workspace(slack_team_id=team)
            session.add(ws)
            await session.flush()
        ws.bot_token = enc
        if bot_user_id:
            ws.bot_user_id = bot_user_id
        if scopes:
            ws.bot_scopes = scopes
        ws.installed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await session.commit()
        ws_id = ws.id
    invalidate_token_cache()
    print(f"installed: team={team} workspace_id={ws_id} bot_user_id={bot_user_id or '(not set)'}")


async def do_list() -> None:
    async with get_session() as session:
        rows = (
            await session.execute(select(Workspace).order_by(Workspace.created_at))
        ).scalars().all()
    if not rows:
        print("(no workspaces)")
        return
    for r in rows:
        installed = r.installed_at.isoformat() if r.installed_at else "(not installed)"
        token_state = "set" if r.bot_token else "MISSING"
        print(f"- team={r.slack_team_id} ws={r.id} name={r.name!r} bot_token={token_state} bot_user_id={r.bot_user_id} installed={installed}")


async def do_uninstall(team: str) -> None:
    async with get_session() as session:
        ws = (
            await session.execute(select(Workspace).where(Workspace.slack_team_id == team))
        ).scalar_one_or_none()
        if ws is None:
            print(f"team {team!r} not found")
            return
        ws.bot_token = None
        ws.bot_user_id = None
        ws.bot_scopes = None
        # Keep the row so all per-tenant data (Spaces, threads, etc.) stays.
        # Just blanks the token; subsequent events for this team won't be authed.
        await session.commit()
    invalidate_token_cache()
    print(f"uninstalled: team={team} (data preserved, token cleared)")


async def do_backfill(team: str) -> None:
    """One-time: take the legacy SLACK_BOT_TOKEN from Doppler and seed the
    workspace row for the given team. Use this exactly once when migrating
    from single-tenant to multi-tenant."""
    settings = get_settings()
    if not settings.slack_bot_token:
        print("SLACK_BOT_TOKEN missing in env; nothing to backfill")
        sys.exit(1)
    await do_install(team=team, token=settings.slack_bot_token, bot_user_id=None, scopes="(backfilled)")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.slack.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_i = sub.add_parser("install")
    p_i.add_argument("--team", required=True, help="Slack team_id, e.g. T0ATZ...")
    p_i.add_argument("--token", required=True, help="Bot token (xoxb-...)")
    p_i.add_argument("--bot-user-id")
    p_i.add_argument("--scopes")

    sub.add_parser("list")

    p_u = sub.add_parser("uninstall")
    p_u.add_argument("--team", required=True)

    p_b = sub.add_parser("backfill")
    p_b.add_argument("--team", required=True, help="Slack team_id to seed with the legacy SLACK_BOT_TOKEN from env")

    args = parser.parse_args()

    if args.cmd == "install":
        asyncio.run(do_install(args.team, args.token, args.bot_user_id, args.scopes))
    elif args.cmd == "list":
        asyncio.run(do_list())
    elif args.cmd == "uninstall":
        asyncio.run(do_uninstall(args.team))
    elif args.cmd == "backfill":
        asyncio.run(do_backfill(args.team))


if __name__ == "__main__":
    main()
