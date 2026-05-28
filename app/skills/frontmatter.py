"""Frontmatter parser + auto-generator for skill uploads.

Behaviour, per spec:

1. If the .md starts with a `---` block, parse it as YAML and pull out `name`,
   `description`, `activation`. Strip the block from the body.
2. For any of those three that are still missing, ask claude-haiku-4-5 (via
   LiteLLM, matching the project's `run_cheap` seam) for a JSON response and
   parse it with a Pydantic strict schema.
3. If the LLM call fails or returns garbage, fall back to defaults derived
   from the filename (slug of the basename) and a generic description.
4. Always extract `[[name]]` slugs from the body into a deduplicated link
   list.

Name slugs are normalised to kebab-case, lower-case, <= 40 chars. Description
is truncated to 280 chars. Activation is coerced to one of the two enum
values; anything weird defaults to `on_demand` (the conservative choice: the
body won't get auto-injected into every prompt).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import litellm
import structlog
import yaml
from langfuse import get_client
from pydantic import BaseModel, Field, ValidationError, field_validator

log = structlog.get_logger(__name__)
_langfuse = get_client()

# Hard-coded per spec: haiku is plenty for JSON metadata extraction. Using a
# specific ID (not get_settings().cheap_model) so this stays predictable even
# if the cheap-model default moves.
FRONTMATTER_MODEL = "claude-haiku-4-5"

# Public, per spec.
NAME_MAX_LEN = 40
DESCRIPTION_MAX_LEN = 280
# Cap the snippet we feed the LLM so a 256 KB body doesn't blow the prompt.
_BODY_SNIPPET_CHARS = 3000

# Matches the leading `---\n...\n---\n` block (greedy on lines, anchored at
# start). We allow `\r\n` line endings since Slack file uploads can pick that
# up from Windows editors.
_FRONTMATTER_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n", re.DOTALL)
# `[[slug]]` cross-references in the body. Slugs are lower-case alnum + dashes.
_LINK_RE = re.compile(r"\[\[([a-z0-9][a-z0-9-]{0,62})\]\]")


@dataclass
class Frontmatter:
    """Resolved metadata + the body with the frontmatter block stripped."""

    name: str
    description: str
    activation: str  # "always_active" | "on_demand"
    body: str
    links: list[str]
    # Audit: where each field came from. Lets us tell the user "we inferred X"
    # vs "your frontmatter said X" in the Slack preview.
    inferred_fields: list[str]


class _GeneratedFrontmatter(BaseModel):
    """Strict schema for the LLM JSON response. Anything off-shape raises."""

    name: str = Field(min_length=1, max_length=NAME_MAX_LEN)
    description: str = Field(min_length=1, max_length=DESCRIPTION_MAX_LEN)
    activation: str

    @field_validator("activation")
    @classmethod
    def _validate_activation(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"always_active", "on_demand"}:
            raise ValueError("activation must be 'always_active' or 'on_demand'")
        return v


def _slugify(text: str) -> str:
    """Kebab-case, lower, alnum + dashes, collapse runs, trim to NAME_MAX_LEN.
    Empty input or pure-noise yields 'skill' as a last resort."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    s = re.sub(r"-+", "-", s)[:NAME_MAX_LEN].strip("-")
    return s or "skill"


def _extract_links(body: str) -> list[str]:
    """Deduplicate while preserving first-seen order."""
    seen: dict[str, None] = {}
    for match in _LINK_RE.finditer(body):
        seen.setdefault(match.group(1), None)
    return list(seen.keys())


def parse_frontmatter_block(raw: str) -> tuple[dict, str]:
    """Pull a YAML frontmatter block off the head of `raw`, return (data, body).
    If no block, returns ({}, raw)."""
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    body = raw[match.end():]
    try:
        loaded = yaml.safe_load(match.group(1)) or {}
        if not isinstance(loaded, dict):
            return {}, body
        return loaded, body
    except yaml.YAMLError:
        # Malformed YAML, treat as no frontmatter; the LLM will fill the gaps.
        return {}, body


async def _generate_missing(
    body_snippet: str, *, need_name: bool, need_description: bool, need_activation: bool
) -> _GeneratedFrontmatter | None:
    """Ask haiku for the missing fields in one JSON-shaped call. Returns None
    on any failure so the caller can fall back."""
    prompt = (
        "Lee este markdown y devolveme JSON estricto con:\n"
        "- name: slug kebab-case de máximo 40 chars, descriptivo del contenido\n"
        "- description: una línea ≤ 280 chars que diga CUÁNDO el agente "
        "debería cargar esta skill (no qué hace, sino cuándo usarla)\n"
        "- activation: \"always_active\" si es behavioral/rules cortas, "
        "\"on_demand\" si es knowledge base / reference grande\n\n"
        "Markdown:\n"
        "---\n"
        f"{body_snippet}\n"
        "---\n\n"
        "Responde SOLO el JSON, nada más."
    )
    try:
        with _langfuse.start_as_current_observation(
            as_type="generation",
            name="skills:frontmatter",
            model=FRONTMATTER_MODEL,
            input=prompt,
        ) as gen:
            response = await litellm.acompletion(
                model=FRONTMATTER_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=256,
            )
            text = (response.choices[0].message.content or "").strip()
            usage = getattr(response, "usage", None)
            if usage is not None:
                gen.update(
                    output=text,
                    usage_details={
                        "input": getattr(usage, "prompt_tokens", 0) or 0,
                        "output": getattr(usage, "completion_tokens", 0) or 0,
                    },
                )
    except Exception as exc:  # noqa: BLE001
        log.warning("frontmatter_llm_call_failed", error=str(exc)[:200])
        return None

    # Strip code fences if the model wrapped the JSON.
    cleaned = text
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        raw = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        log.warning("frontmatter_llm_bad_json", error=str(exc), sample=cleaned[:200])
        return None
    try:
        parsed = _GeneratedFrontmatter(**raw)
    except (ValidationError, TypeError) as exc:
        log.warning("frontmatter_llm_bad_shape", error=str(exc), sample=cleaned[:200])
        return None
    log.info(
        "skill_frontmatter_generated",
        name=parsed.name,
        activation=parsed.activation,
        need_name=need_name,
        need_description=need_description,
        need_activation=need_activation,
    )
    return parsed


def _filename_defaults(filename: str | None) -> tuple[str, str, str]:
    """Last-resort defaults if the LLM is unreachable. Filename slug for name,
    generic description, conservative on_demand activation."""
    base = (filename or "skill").rsplit(".", 1)[0]
    name = _slugify(base)
    description = f"Skill cargada desde {filename or 'archivo sin nombre'}."
    return name, description[:DESCRIPTION_MAX_LEN], "on_demand"


def _coerce_activation(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip().lower().replace("-", "_")
    if v in {"always_active", "always", "active"}:
        return "always_active"
    if v in {"on_demand", "ondemand", "lazy", "demand"}:
        return "on_demand"
    return None


async def resolve_frontmatter(raw_markdown: str, filename: str | None = None) -> Frontmatter:
    """Top-level: parse user-supplied frontmatter, fill the gaps with one LLM
    call (or filename fallback), extract `[[links]]`. Returns the canonical
    record ready to persist."""
    user_fm, body = parse_frontmatter_block(raw_markdown)
    raw_name = user_fm.get("name") if isinstance(user_fm.get("name"), str) else None
    raw_desc = (
        user_fm.get("description") if isinstance(user_fm.get("description"), str) else None
    )
    raw_activation = _coerce_activation(user_fm.get("activation"))

    need_name = not raw_name
    need_desc = not raw_desc
    need_activation = raw_activation is None
    inferred_fields: list[str] = []

    name = raw_name
    description = raw_desc
    activation = raw_activation

    if need_name or need_desc or need_activation:
        generated = await _generate_missing(
            body[:_BODY_SNIPPET_CHARS],
            need_name=need_name,
            need_description=need_desc,
            need_activation=need_activation,
        )
        if generated is not None:
            if need_name:
                name = generated.name
                inferred_fields.append("name")
            if need_desc:
                description = generated.description
                inferred_fields.append("description")
            if need_activation:
                activation = generated.activation
                inferred_fields.append("activation")
        else:
            fb_name, fb_desc, fb_activation = _filename_defaults(filename)
            if need_name:
                name = fb_name
                inferred_fields.append("name")
            if need_desc:
                description = fb_desc
                inferred_fields.append("description")
            if need_activation:
                activation = fb_activation
                inferred_fields.append("activation")

    # Final normalisation: even if the user supplied values, we tighten them.
    name = _slugify(name or "skill")
    description = (description or "").strip()[:DESCRIPTION_MAX_LEN] or "(sin descripción)"
    if activation not in {"always_active", "on_demand"}:
        activation = "on_demand"

    return Frontmatter(
        name=name,
        description=description,
        activation=activation,
        body=body,
        links=_extract_links(body),
        inferred_fields=inferred_fields,
    )
