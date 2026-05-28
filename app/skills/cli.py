"""Skill registry CLI. Skills are data: `register` uploads any package directory
(manifest.json + SKILL.md + optional resources/) to the registry; the demo skill
is seeded through this exact path, with no special-casing.

Run under Doppler, e.g.:
  doppler run -- uv run python -m app.skills.cli register skill_packages/chart-from-data
  doppler run -- uv run python -m app.skills.cli install chart-from-data --team T0...
  doppler run -- uv run python -m app.skills.cli uninstall chart-from-data --team T0...
  doppler run -- uv run python -m app.skills.cli list --team T0...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.db.models import Workspace
from app.db.session import get_session
from app.skills import registry


async def _resolve_workspace(team_id: str):
    async with get_session() as session:
        ws = (
            await session.execute(select(Workspace).where(Workspace.slack_team_id == team_id))
        ).scalar_one_or_none()
    if ws is None:
        print(f"No existe workspace para team {team_id!r}. Escribile al bot primero.")
        sys.exit(1)
    return ws.id


async def do_register(path: str) -> None:
    pkg = Path(path)
    manifest = json.loads((pkg / "manifest.json").read_text())
    skill_md = (pkg / "SKILL.md").read_text()
    resources: dict[str, bytes] = {}
    res_dir = pkg / "resources"
    if res_dir.is_dir():
        for f in res_dir.iterdir():
            if f.is_file():
                resources[f.name] = f.read_bytes()
    name = await registry.register_skill(manifest, skill_md, resources or None)
    print(f"Skill registrada en el catálogo: {name} (v{manifest.get('version', '?')})")


async def do_install(name: str, team: str) -> None:
    ws = await _resolve_workspace(team)
    await registry.install(ws, name)
    print(f"Instalada '{name}' en workspace {ws}")


async def do_uninstall(name: str, team: str) -> None:
    ws = await _resolve_workspace(team)
    await registry.uninstall(ws, name)
    print(f"Desinstalada '{name}' en workspace {ws}")


async def do_list(team: str) -> None:
    ws = await _resolve_workspace(team)
    skills = await registry.list_installed(ws)
    if not skills:
        print("(ninguna skill instalada)")
    for s in skills:
        print(f"- {s.name} v{s.version}: {s.description}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.skills.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_r = sub.add_parser("register")
    p_r.add_argument("path", help="Directory with manifest.json + SKILL.md")
    p_i = sub.add_parser("install")
    p_i.add_argument("name")
    p_i.add_argument("--team", required=True)
    p_u = sub.add_parser("uninstall")
    p_u.add_argument("name")
    p_u.add_argument("--team", required=True)
    p_l = sub.add_parser("list")
    p_l.add_argument("--team", required=True)
    args = parser.parse_args()

    if args.cmd == "register":
        asyncio.run(do_register(args.path))
    elif args.cmd == "install":
        asyncio.run(do_install(args.name, args.team))
    elif args.cmd == "uninstall":
        asyncio.run(do_uninstall(args.name, args.team))
    elif args.cmd == "list":
        asyncio.run(do_list(args.team))


if __name__ == "__main__":
    main()
