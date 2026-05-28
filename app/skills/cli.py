"""Dev CLI for the Skills feature. Mirrors the Slack slash-command surface so
maintainers can seed / inspect skills without booting Slack. Production users
should always go through `/sebitas skill ...`.

Usage (under Doppler so R2 + DB credentials are present):

  doppler run -- uv run python -m app.skills.cli upload path/to/file.md \\
      --team T0... --slack-user U0...
  doppler run -- uv run python -m app.skills.cli list --team T0... --slack-user U0...
  doppler run -- uv run python -m app.skills.cli remove agent-way-of-work \\
      --team T0... --slack-user U0...
  doppler run -- uv run python -m app.skills.cli load agent-way-of-work \\
      --team T0... --slack-user U0...

The `upload` subcommand runs the same frontmatter resolution path as Slack
(YAML parse + LLM fill + link extraction), so a file ingested via CLI ends
up indistinguishable from one ingested via Slack.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

from app.db.models import AppUser, Workspace
from app.db.session import get_session
from app.db.repository import upsert_app_user, upsert_workspace
from app.skills import registry
from app.skills.frontmatter import resolve_frontmatter


async def _resolve(team_id: str, slack_user_id: str) -> tuple[Workspace, AppUser]:
    async with get_session() as session:
        ws = await upsert_workspace(session, team_id)
        user = await upsert_app_user(session, ws.id, slack_user_id)
        await session.commit()
        # Refresh detached identifiers for the caller.
        ws = (await session.execute(select(Workspace).where(Workspace.id == ws.id))).scalar_one()
        user = (await session.execute(select(AppUser).where(AppUser.id == user.id))).scalar_one()
        return ws, user


async def do_upload(path: str, team_id: str, slack_user_id: str) -> None:
    raw = Path(path).read_text()
    ws, user = await _resolve(team_id, slack_user_id)
    fm = await resolve_frontmatter(raw, filename=Path(path).name)
    size_bytes = len(fm.body.encode("utf-8"))
    try:
        skill = await registry.create_skill(
            workspace_id=ws.id,
            name=fm.name,
            description=fm.description,
            activation_default=fm.activation,  # type: ignore[arg-type]
            body=fm.body,
            links=fm.links,
            size_bytes=size_bytes,
            created_by_user_id=user.id,
        )
    except registry.SkillNameTaken as exc:
        print(f"error: {exc}")
        sys.exit(2)
    await registry.install_for_user(user_id=user.id, skill_id=skill.id)
    print(
        f"installed {fm.name!r} (id {skill.id}, {size_bytes}B, "
        f"activation={fm.activation}, inferred={fm.inferred_fields or '-'})"
    )


async def do_list(team_id: str, slack_user_id: str) -> None:
    _, user = await _resolve(team_id, slack_user_id)
    installs = await registry.list_for_user(user.id)
    if not installs:
        print("(no skills installed for this user)")
        return
    for swi in installs:
        print(
            f"- {swi.skill.name} [{swi.effective_activation}] "
            f"v{swi.skill.version} {swi.skill.size_bytes}B: {swi.skill.description}"
        )


async def do_remove(name: str, team_id: str, slack_user_id: str) -> None:
    _, user = await _resolve(team_id, slack_user_id)
    swi = await registry.get_skill_for_user(user.id, name)
    if swi is None:
        print(f"not installed: {name}")
        sys.exit(1)
    await registry.uninstall_for_user(user_id=user.id, skill_id=swi.skill.id)
    print(f"uninstalled {name}")


async def do_load(name: str, team_id: str, slack_user_id: str) -> None:
    _, user = await _resolve(team_id, slack_user_id)
    try:
        loaded = await registry.load_skill_body_for_user(user.id, name)
    except registry.SkillNotFound as exc:
        print(f"error: {exc}")
        sys.exit(1)
    print(f"# {loaded.name}\n{loaded.description}\n---\n{loaded.body}")
    if loaded.missing_links:
        print("\n[missing links: " + ", ".join(loaded.missing_links) + "]")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.skills.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("upload")
    p.add_argument("path")
    p.add_argument("--team", required=True)
    p.add_argument("--slack-user", required=True)

    p = sub.add_parser("list")
    p.add_argument("--team", required=True)
    p.add_argument("--slack-user", required=True)

    p = sub.add_parser("remove")
    p.add_argument("name")
    p.add_argument("--team", required=True)
    p.add_argument("--slack-user", required=True)

    p = sub.add_parser("load")
    p.add_argument("name")
    p.add_argument("--team", required=True)
    p.add_argument("--slack-user", required=True)

    args = parser.parse_args()
    if args.cmd == "upload":
        asyncio.run(do_upload(args.path, args.team, args.slack_user))
    elif args.cmd == "list":
        asyncio.run(do_list(args.team, args.slack_user))
    elif args.cmd == "remove":
        asyncio.run(do_remove(args.name, args.team, args.slack_user))
    elif args.cmd == "load":
        asyncio.run(do_load(args.name, args.team, args.slack_user))


if __name__ == "__main__":
    main()
