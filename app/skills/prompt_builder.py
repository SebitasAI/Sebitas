"""Build the skills section of the system prompt for a single user.

Two-section format (always wrapped in XML so the model distinguishes skill
content from user instructions, mitigating prompt injection):

    <memory scope="company">{body}</memory>
    <memory scope="team">{body}</memory>
    <memory scope="user">{body}</memory>

    <always_active_skills>
    <skill name="...">
    {body}
    </skill>
    <skill name="...">
    {body}
    </skill>
    </always_active_skills>

    <available_skills>
    You can load these skills using `load_skill(name="<name>")` when relevant.
    - <name>: <description>
    - <name>: <description>
    </available_skills>

The memory section (slice T-X Phase A) is loaded directly from the workspace
by reserved slug, not via SkillInstall. It's always-on for the calling user
regardless of activation_default, since the whole point of persistent memory
is that the agent doesn't have to opt in to remember the user.

Caps (per the agreed numbers):

- 8K tokens worth of always-active bodies = soft warning, log
  `skill_discovery_overflow` + keep oldest-installed-first that still fit.
- 20K tokens hard cap = degrade the rest to on-demand for this request.

Memory bodies are NOT subject to the regular skill caps (they have their own
MAX_BODY_BYTES = 200KB cap in append.py); they're always injected. If memory
ever grows out of hand, compaction (Phase C) is the answer, not gating it
behind the soft/hard caps.

Token counting uses a simple ~4 chars-per-token heuristic. Good enough for a
budget; precise counting would need tiktoken-equivalent which we don't ship.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select

from app.db.models import Skill
from app.db.session import get_session
from app.memory.constants import COMPANY_SLUG, TEAM_SLUG, user_slug
from app.skills import registry, storage

log = structlog.get_logger(__name__)

# Token estimate: ~4 chars / token for English/Spanish prose. Picked to be
# slightly pessimistic so we under-pack rather than overshoot the cap.
_CHARS_PER_TOKEN = 4
SOFT_TOKEN_CAP = 8_000
HARD_TOKEN_CAP = 20_000


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def _xml_escape_attr(value: str) -> str:
    """Skill names are slugs (alnum + dash), so this is mostly defensive. Still
    quote-encode in case of weird future names."""
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


async def _load_memory_blocks(
    workspace_id: uuid.UUID,
    slack_user_id: str | None,
) -> list[tuple[str, str]]:
    """Return [(scope_label, body), ...] for the three reserved memory
    slugs that exist for this workspace. Order: company, team, user.

    Best-effort: a missing skill row is skipped (the workspace may not
    have been re-seeded yet, or this is the user's first message and
    the upstream seed call hasn't committed). An R2 download failure
    is logged and skipped -- the agent still gets the other blocks.
    """
    names: list[tuple[str, str]] = [
        ("company", COMPANY_SLUG),
        ("team", TEAM_SLUG),
    ]
    if slack_user_id:
        names.append(("user", user_slug(slack_user_id)))

    slugs = [n[1] for n in names]
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace_id,
                    Skill.name.in_(slugs),
                    Skill.source == "memory",
                )
            )
        ).scalars().all()
    by_name = {r.name: r for r in rows}

    blocks: list[tuple[str, str]] = []
    for scope_label, slug in names:
        skill = by_name.get(slug)
        if skill is None:
            continue
        try:
            body = await storage.download_skill_body(
                workspace_id=skill.workspace_id,
                skill_id=skill.id,
                version=skill.version,
                r2_ref=skill.body_r2_ref,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "memory_block_load_failed",
                workspace_id=str(workspace_id),
                skill_name=slug,
                error=str(exc)[:200],
            )
            continue
        blocks.append((scope_label, body))
    return blocks


async def build_skills_context(
    user_id: uuid.UUID | None,
    *,
    workspace_id: uuid.UUID | None = None,
    slack_user_id: str | None = None,
) -> str:
    """Return the full skills system-prompt fragment for the given user.

    Renders (in order, any of which may be absent):
      1. `<memory scope="...">` blocks for company / team / users/<id>.
         Requires `workspace_id`. If only `workspace_id` is provided, the
         per-user memory is omitted (still fine; company + team go through).
      2. `<always_active_skills>` for installed always-active skills.
      3. `<available_skills>` listing of on-demand skills (and degraded
         always-active ones that didn't fit the token cap).

    Returns "" when there's no skill context at all -- caller can omit
    the section from the system prompt entirely without an empty wrapper.
    """
    memory_blocks: list[tuple[str, str]] = []
    if workspace_id is not None:
        try:
            memory_blocks = await _load_memory_blocks(workspace_id, slack_user_id)
        except Exception as exc:  # noqa: BLE001
            # Memory is best-effort: never break the prompt build over it.
            log.warning(
                "memory_blocks_load_failed",
                workspace_id=str(workspace_id),
                error=str(exc)[:200],
            )
            memory_blocks = []

    if user_id is None:
        return _render([], [], memory_blocks)

    installs = await registry.list_for_user(user_id)

    # Two buckets. We keep the install order (most-recent first) for `on_demand`
    # since that affects rendering. For always_active we re-sort by
    # installed_at ASC so we pack oldest-first (least disruptive churn).
    always_active = sorted(
        [s for s in installs if s.effective_activation == "always_active"],
        key=lambda s: s.install.installed_at,
    )
    on_demand = [s for s in installs if s.effective_activation == "on_demand"]

    bodies: list[tuple[str, str]] = []  # (name, body)
    degraded: list[str] = []  # always_active that didn't fit and got pushed to descriptions
    total_tokens = 0
    soft_warned = False

    for swi in always_active:
        try:
            body = await storage.download_skill_body(
                workspace_id=swi.skill.workspace_id,
                skill_id=swi.skill.id,
                version=swi.skill.version,
                r2_ref=swi.skill.body_r2_ref,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "skill_body_load_failed",
                user_id=str(user_id),
                skill_id=str(swi.skill.id),
                skill_name=swi.skill.name,
                error=str(exc)[:200],
            )
            # Body unreachable: skip silently so the rest of the prompt builds.
            continue
        body_tokens = _estimate_tokens(body)
        if total_tokens + body_tokens > HARD_TOKEN_CAP:
            degraded.append(swi.skill.name)
            continue
        if total_tokens + body_tokens > SOFT_TOKEN_CAP and not soft_warned:
            log.warning(
                "skill_discovery_soft_cap",
                user_id=str(user_id),
                tokens=total_tokens + body_tokens,
                cap=SOFT_TOKEN_CAP,
            )
            soft_warned = True
        bodies.append((swi.skill.name, body))
        total_tokens += body_tokens
        log.info(
            "skill_loaded",
            user_id=str(user_id),
            skill_id=str(swi.skill.id),
            skill_name=swi.skill.name,
            source="system_prompt",
            size_bytes=swi.skill.size_bytes,
        )

    if degraded:
        log.warning(
            "skill_discovery_overflow",
            user_id=str(user_id),
            hard_cap_tokens=HARD_TOKEN_CAP,
            total_tokens_after_truncation=total_tokens,
            degraded_count=len(degraded),
            degraded_names=degraded,
        )

    # The on_demand section also lists any degraded skills so the model can
    # still reach them via load_skill even though they didn't fit always-active.
    on_demand_entries: list[tuple[str, str]] = [
        (s.skill.name, s.skill.description) for s in on_demand
    ]
    if degraded:
        # Look up descriptions for the degraded ones in the original always_active list.
        for swi in always_active:
            if swi.skill.name in degraded:
                on_demand_entries.append((swi.skill.name, swi.skill.description))

    return _render(bodies, on_demand_entries, memory_blocks)


def _render(
    always_active_bodies: list[tuple[str, str]],
    on_demand_entries: list[tuple[str, str]],
    memory_blocks: list[tuple[str, str]] | None = None,
) -> str:
    parts: list[str] = []

    if memory_blocks:
        chunks: list[str] = []
        for scope_label, body in memory_blocks:
            chunks.append(f'<memory scope="{_xml_escape_attr(scope_label)}">')
            chunks.append(body.strip())
            chunks.append("</memory>")
        parts.append("\n".join(chunks))

    if always_active_bodies:
        chunks = ["<always_active_skills>"]
        for name, body in always_active_bodies:
            chunks.append(f'<skill name="{_xml_escape_attr(name)}">')
            chunks.append(body.strip())
            chunks.append("</skill>")
        chunks.append("</always_active_skills>")
        parts.append("\n".join(chunks))

    if on_demand_entries:
        lines = [
            "<available_skills>",
            "Skills disponibles. Cargá la que aplique con la tool "
            "`load_skill(name=\"<name>\")`; cargá solo las que te hagan falta.",
        ]
        for name, desc in on_demand_entries:
            lines.append(f"- {name}: {desc}")
        lines.append("</available_skills>")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)
