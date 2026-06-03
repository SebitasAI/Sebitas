"""One-shot: refine the description of `misterr-bi-agent-training-simetrik`.

Context: a Simetrik agent run hit `agent_max_iterations=25` mid-task and
left `[tool_use]` as the final assistant block (Langfuse session
`TH3PWEDA7:C0B7558UEHG:1780441242.142459`). Two contributing factors:

  1. Cap was too low for that workflow.
  2. The skill `misterr-bi-agent-training-simetrik` was loading INSTEAD
     of `bi-agent-way-of-work-simetrik` for queries that should have
     used the latter. Both had overlapping descriptions ("reportes,
     dashboards o análisis").

Fix (1) is the `agent_max_iterations 25 -> 35` config bump in the same
PR. Fix (2) is this script: tighten the new skill's description so it
ONLY loads when the user is BUILDING a new dashboard from scratch,
leaving the customer-analysis case to the other skill.

Usage:
  doppler run -p sebitas -c prd -- python -m scripts.refine_bi_training_skill_description

Safe to run multiple times: prints before/after and only writes if the
description actually changed."""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.models import Skill, Workspace
from app.db.session import get_session


WORKSPACE_NAME = "Simetrik"
SKILL_NAME = "misterr-bi-agent-training-simetrik"

NEW_DESCRIPTION = (
    "Carga esta skill SOLO cuando el usuario pide CONSTRUIR un dashboard "
    "NUEVO desde cero o ejecutar SQL nativo en Snowflake via la Metabase "
    "API. NO la cargues para analizar datos de un cliente existente -- "
    "en ese caso usa `bi-agent-way-of-work-simetrik` (tiene reglas de "
    "qué tablas atacar y cuándo responder 'no se puede'). Esta skill "
    "contiene técnicas de SQL nativo + construcción de cards/dashboards "
    "sin depender de Card IDs existentes."
)


async def main() -> None:
    async with get_session() as session:
        ws = (
            await session.execute(
                select(Workspace).where(Workspace.name == WORKSPACE_NAME)
            )
        ).scalar_one_or_none()
        if ws is None:
            print(f"Workspace {WORKSPACE_NAME!r} not found.")
            return

        skill = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == ws.id,
                    Skill.name == SKILL_NAME,
                )
            )
        ).scalar_one_or_none()
        if skill is None:
            print(f"Skill {SKILL_NAME!r} not found in workspace.")
            return

        print(f"=== BEFORE (workspace={WORKSPACE_NAME}, skill={SKILL_NAME}) ===")
        print(f"description:\n  {skill.description}\n")

        if skill.description == NEW_DESCRIPTION:
            print("No change needed; description already matches.")
            return

        skill.description = NEW_DESCRIPTION
        await session.commit()

        print("=== AFTER ===")
        print(f"description:\n  {skill.description}\n")
        print("Updated successfully.")


if __name__ == "__main__":
    asyncio.run(main())
