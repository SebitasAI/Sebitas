"""Unit tests for app.skills.frontmatter.

Covers: LLM-driven generation when frontmatter is absent, respect for user
values when present, link extraction, fallback when the LLM returns garbage,
filename-default fallback when LiteLLM errors out, and slug + length
normalisation.
"""

from __future__ import annotations

import pytest

from app.skills.frontmatter import resolve_frontmatter


@pytest.mark.asyncio
async def test_upload_md_without_frontmatter_generates_metadata(patch_litellm):
    """No frontmatter -> single LLM call -> all three fields filled."""
    patch_litellm(
        reply_text=(
            '{"name": "datalake-guide", '
            '"description": "Usar cuando el usuario pregunte sobre tablas del datalake.", '
            '"activation": "on_demand"}'
        )
    )
    body = "# Datalake guide\n\nReglas para consultar el datalake."
    fm = await resolve_frontmatter(body, filename="datalake_guide.md")
    assert fm.name == "datalake-guide"
    assert "datalake" in fm.description.lower()
    assert fm.activation == "on_demand"
    # All three came from the LLM.
    assert set(fm.inferred_fields) == {"name", "description", "activation"}
    # Body is untouched (no frontmatter block to strip).
    assert fm.body == body


@pytest.mark.asyncio
async def test_upload_md_with_frontmatter_respects_user_values(patch_litellm):
    """Full frontmatter -> no LLM call -> all user values preserved."""
    mock = patch_litellm(reply_text='{"name": "fallback", "description": "no", "activation": "on_demand"}')
    raw = (
        "---\n"
        "name: agent-way-of-work\n"
        "description: Cuándo seguir las reglas de trabajo del equipo.\n"
        "activation: always_active\n"
        "---\n"
        "Cuerpo de la skill."
    )
    fm = await resolve_frontmatter(raw, filename="AGENT_WAY_OF_WORK.md")
    assert fm.name == "agent-way-of-work"
    assert fm.description.startswith("Cuándo seguir")
    assert fm.activation == "always_active"
    assert fm.inferred_fields == []
    # Frontmatter block stripped from the body.
    assert "name:" not in fm.body
    assert fm.body.strip() == "Cuerpo de la skill."
    # No LLM call needed.
    assert mock.call_count == 0


@pytest.mark.asyncio
async def test_partial_frontmatter_fills_only_missing(patch_litellm):
    """name present, description + activation inferred. Verify the LLM is
    called once and only the missing fields are taken from its reply."""
    mock = patch_litellm(
        reply_text=(
            '{"name": "ignored-by-merge", '
            '"description": "Inferida.", '
            '"activation": "always_active"}'
        )
    )
    raw = "---\nname: my-skill\n---\nbody"
    fm = await resolve_frontmatter(raw, filename="x.md")
    assert fm.name == "my-skill"  # user value wins
    assert fm.description == "Inferida."
    assert fm.activation == "always_active"
    assert set(fm.inferred_fields) == {"description", "activation"}
    assert mock.call_count == 1


@pytest.mark.asyncio
async def test_llm_garbage_falls_back_to_filename(patch_litellm):
    """If the LLM returns non-JSON we fall back to filename-derived defaults."""
    patch_litellm(reply_text="not json")
    fm = await resolve_frontmatter("# body", filename="My Cool Skill.md")
    assert fm.name == "my-cool-skill"
    assert fm.activation == "on_demand"  # conservative fallback
    assert set(fm.inferred_fields) == {"name", "description", "activation"}


@pytest.mark.asyncio
async def test_llm_exception_falls_back_to_filename(patch_litellm):
    """Same fallback for transport errors."""
    patch_litellm(side_effect=RuntimeError("boom"))
    fm = await resolve_frontmatter("# body", filename="onboarding.md")
    assert fm.name == "onboarding"
    assert fm.activation == "on_demand"


@pytest.mark.asyncio
async def test_links_are_extracted_and_deduplicated(patch_litellm):
    """`[[slug]]` references are collected in order, dedup'd."""
    patch_litellm(
        reply_text='{"name": "x", "description": "d", "activation": "on_demand"}'
    )
    body = (
        "ver [[datalake-guide]] y [[onboarding]]. también [[datalake-guide]] de nuevo."
    )
    fm = await resolve_frontmatter(body, filename="x.md")
    assert fm.links == ["datalake-guide", "onboarding"]


@pytest.mark.asyncio
async def test_name_is_slugified_and_truncated(patch_litellm):
    """User-supplied name is normalised to kebab-case + capped at 40 chars."""
    raw = (
        "---\n"
        "name: 'Very Long Skill Name With UPPER and  spaces  galore!!!'\n"
        "description: x\n"
        "activation: on_demand\n"
        "---\n"
        "body"
    )
    patch_litellm(reply_text="{}")
    fm = await resolve_frontmatter(raw, filename="x.md")
    assert fm.name == "very-long-skill-name-with-upper-and-spac"
    assert len(fm.name) <= 40
