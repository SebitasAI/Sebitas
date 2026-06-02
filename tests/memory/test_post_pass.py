"""Tests for app.memory.post_pass (slice T-X Phase B).

The post-pass is fire-and-forget. Test surface:

- Pure: `_trim` truncation, prompt-output parsing (JSON array, code-fence
  strip, malformed -> []).
- Integration: `extract_and_persist` writes to the right skill per scope,
  caps at MAX_FACTS_PER_TURN, swallows model errors.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import Skill
from app.db.session import get_session
from app.memory import post_pass, seed
from app.memory.constants import COMPANY_SLUG, TEAM_SLUG, user_slug


pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Pure unit tests (no DB, no LLM)
# --------------------------------------------------------------------------- #


def test_trim_under_cap_unchanged():
    out = post_pass._trim("short", cap=100)
    assert out == "short"


def test_trim_over_cap_truncates_with_ellipsis():
    text = "a" * 200
    out = post_pass._trim(text, cap=50)
    assert out.endswith("…")
    assert len(out) <= 51


def test_trim_strips_whitespace():
    assert post_pass._trim("   hello   ") == "hello"


def test_trim_empty_returns_empty():
    assert post_pass._trim("") == ""


# --------------------------------------------------------------------------- #
# _extract parser tests
# --------------------------------------------------------------------------- #


def _make_resp(content: str):
    class _Msg:
        def __init__(self, c):
            self.content = c

    class _Choice:
        def __init__(self, c):
            self.message = _Msg(c)

    class _Usage:
        prompt_tokens = 10
        completion_tokens = 5

    class _R:
        def __init__(self, c):
            self.choices = [_Choice(c)]
            self.usage = _Usage()

    return _R(content)


@pytest.mark.asyncio
async def test_extract_parses_valid_json(monkeypatch):
    async def _fake(model, messages, **kwargs):
        return _make_resp('[{"scope":"user","fact":"Sam habla español"},'
                          '{"scope":"company","fact":"Antiff es B2B"}]')
    monkeypatch.setattr(post_pass.litellm, "acompletion", _fake)
    out = await post_pass._extract("hola", "qué tal")
    assert len(out) == 2
    assert out[0]["scope"] == "user"
    assert out[1]["scope"] == "company"


@pytest.mark.asyncio
async def test_extract_strips_code_fences(monkeypatch):
    async def _fake(model, messages, **kwargs):
        return _make_resp('```json\n[{"scope":"user","fact":"x"}]\n```')
    monkeypatch.setattr(post_pass.litellm, "acompletion", _fake)
    out = await post_pass._extract("a", "b")
    assert out == [{"scope": "user", "fact": "x"}]


@pytest.mark.asyncio
async def test_extract_returns_empty_on_bad_json(monkeypatch):
    async def _fake(model, messages, **kwargs):
        return _make_resp("Aquí está mi análisis del turno...")
    monkeypatch.setattr(post_pass.litellm, "acompletion", _fake)
    out = await post_pass._extract("a", "b")
    assert out == []


@pytest.mark.asyncio
async def test_extract_drops_invalid_scope(monkeypatch):
    async def _fake(model, messages, **kwargs):
        return _make_resp('[{"scope":"galaxy","fact":"x"},'
                          '{"scope":"user","fact":"valid"}]')
    monkeypatch.setattr(post_pass.litellm, "acompletion", _fake)
    out = await post_pass._extract("a", "b")
    assert out == [{"scope": "user", "fact": "valid"}]


@pytest.mark.asyncio
async def test_extract_caps_at_max_facts(monkeypatch):
    items = ",".join(f'{{"scope":"user","fact":"f{i}"}}' for i in range(20))
    async def _fake(model, messages, **kwargs):
        return _make_resp(f"[{items}]")
    monkeypatch.setattr(post_pass.litellm, "acompletion", _fake)
    out = await post_pass._extract("a", "b")
    assert len(out) == post_pass.MAX_FACTS_PER_TURN


@pytest.mark.asyncio
async def test_extract_skips_when_both_sides_empty():
    """No model call when there's nothing to extract from."""
    out = await post_pass._extract("", "   ")
    assert out == []


@pytest.mark.asyncio
async def test_extract_swallows_model_exception(monkeypatch):
    async def _fake(model, messages, **kwargs):
        raise RuntimeError("haiku exploded")
    monkeypatch.setattr(post_pass.litellm, "acompletion", _fake)
    out = await post_pass._extract("a", "b")
    assert out == []


# --------------------------------------------------------------------------- #
# Integration: extract_and_persist
# --------------------------------------------------------------------------- #


@pytest.mark.integration
@pytest.mark.asyncio
async def test_extract_and_persist_writes_to_correct_skills(
    fake_r2, db_session, workspace, user_a, monkeypatch
):
    await seed.ensure_company_skill(workspace.id)
    await seed.ensure_team_skill(workspace.id)
    await seed.ensure_user_skill(workspace.id, user_a.id, user_a.slack_user_id)

    async def _fake_extract(user_text, agent_response):
        return [
            {"scope": "user", "fact": "Sam usa Folk CRM"},
            {"scope": "team", "fact": "Laura es la PM"},
            {"scope": "company", "fact": "El stack es Postgres + dbt"},
        ]
    monkeypatch.setattr(post_pass, "_extract", _fake_extract)

    counts = await post_pass.extract_and_persist(
        workspace_id=workspace.id,
        slack_user_id=user_a.slack_user_id,
        user_text="quiero que sepas...",
        agent_response="entendido",
    )
    assert counts["facts_extracted"] == 3
    assert counts["facts_written"] == 3

    from app.skills import storage as skill_storage

    # Each fact landed in the correct skill body.
    for slug, expected in [
        (user_slug(user_a.slack_user_id), "Sam usa Folk CRM"),
        (TEAM_SLUG, "Laura es la PM"),
        (COMPANY_SLUG, "El stack es Postgres + dbt"),
    ]:
        async with get_session() as session:
            skill = (
                await session.execute(
                    select(Skill).where(
                        Skill.workspace_id == workspace.id,
                        Skill.name == slug,
                    )
                )
            ).scalar_one()
        body = await skill_storage.download_skill_body(
            workspace_id=skill.workspace_id, skill_id=skill.id,
            version=skill.version, r2_ref=skill.body_r2_ref,
        )
        assert expected in body, f"{expected!r} missing from {slug}"
        # Source tag tells compaction where the fact came from.
        assert "[post-pass]" in body


@pytest.mark.integration
@pytest.mark.asyncio
async def test_extract_and_persist_returns_zero_on_no_facts(
    fake_r2, db_session, workspace, user_a, monkeypatch
):
    async def _fake_extract(user_text, agent_response):
        return []
    monkeypatch.setattr(post_pass, "_extract", _fake_extract)

    counts = await post_pass.extract_and_persist(
        workspace_id=workspace.id,
        slack_user_id=user_a.slack_user_id,
        user_text="hola",
        agent_response="hola, ¿qué necesitás?",
    )
    assert counts == {"facts_extracted": 0, "facts_written": 0}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_extract_and_persist_handles_model_error_gracefully(
    fake_r2, db_session, workspace, user_a, monkeypatch
):
    async def _fake_extract(user_text, agent_response):
        raise RuntimeError("boom")
    monkeypatch.setattr(post_pass, "_extract", _fake_extract)

    counts = await post_pass.extract_and_persist(
        workspace_id=workspace.id,
        slack_user_id=user_a.slack_user_id,
        user_text="x", agent_response="y",
    )
    assert counts["facts_extracted"] == 0
    assert counts["facts_written"] == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_extract_and_persist_skips_user_scope_without_user_skill(
    fake_r2, db_session, workspace, user_a, monkeypatch
):
    """If the user skill doesn't exist yet (edge case: first message
    failed seeding), the append for scope=user just no-ops -- we don't
    block the team/company writes."""
    await seed.ensure_company_skill(workspace.id)
    await seed.ensure_team_skill(workspace.id)
    # Note: NO ensure_user_skill call.

    async def _fake_extract(user_text, agent_response):
        return [
            {"scope": "user", "fact": "Sam usa Vim"},
            {"scope": "company", "fact": "Producto en YC W26"},
        ]
    monkeypatch.setattr(post_pass, "_extract", _fake_extract)

    counts = await post_pass.extract_and_persist(
        workspace_id=workspace.id,
        slack_user_id=user_a.slack_user_id,
        user_text="x", agent_response="y",
    )
    # Company fact lands; user fact silently dropped (append returns False).
    assert counts["facts_extracted"] == 2
    assert counts["facts_written"] == 1
