"""Build the skills section of the system prompt for a single user.

Two-section format (always wrapped in XML so the model distinguishes skill
content from user instructions, mitigating prompt injection):

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

Caps (per the agreed numbers):

- 8K tokens worth of always-active bodies = soft warning, log
  `skill_discovery_overflow` + keep oldest-installed-first that still fit.
- 20K tokens hard cap = degrade the rest to on-demand for this request.

Token counting uses a simple ~4 chars-per-token heuristic. Good enough for a
budget; precise counting would need tiktoken-equivalent which we don't ship.
"""

from __future__ import annotations

import uuid

import structlog

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


async def build_skills_context(user_id: uuid.UUID | None) -> str:
    """Return the full skills system-prompt fragment for the given user.
    Empty string when there's no user context or the user has no skills."""
    if user_id is None:
        return ""

    installs = await registry.list_for_user(user_id)
    if not installs:
        return ""

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

    return _render(bodies, on_demand_entries)


def _render(
    always_active_bodies: list[tuple[str, str]],
    on_demand_entries: list[tuple[str, str]],
) -> str:
    parts: list[str] = []
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
