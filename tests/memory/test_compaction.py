"""Tests for app.memory.compaction (slice T-X Phase C).

Two layers:

- Unit tests on `_split_sections` + `_build_compacted_body` + `_is_eligible`
  parsers. Pure functions, no DB, no LLM.
- Integration tests for `compact_skill` (requires TEST_DATABASE_URL).
  Mocks the haiku call via monkeypatching `_call_compaction_model`, so the
  test exercises the read-body / version-check / write-body path without
  hitting LiteLLM.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import Skill
from app.db.session import get_session
from app.memory import compaction, seed
from app.memory.constants import COMPANY_SLUG


# --------------------------------------------------------------------------- #
# Pure unit tests
# --------------------------------------------------------------------------- #


def test_split_sections_full_body():
    body = (
        "<!-- bootstrapped: 2026-06-01T00:00:00Z -->\n"
        "## Curated summary\n"
        "Sam usa Folk CRM.\n"
        "Antiff vende chargebacks.\n"
        "\n"
        "## Observations log\n"
        "- 2026-06-02T14:23Z [explicit-remember]: Sam habla español\n"
        "- 2026-06-02T18:45Z [explicit-remember]: usuarios prefieren Loom\n"
    )
    curated, bullets, leading = compaction._split_sections(body)
    assert "Sam usa Folk CRM" in curated
    assert "Antiff vende chargebacks" in curated
    assert "## Observations log" not in curated
    assert len(bullets) == 2
    assert bullets[0].endswith("Sam habla español")
    assert "bootstrapped" in leading


def test_split_sections_missing_log():
    body = "<!-- bootstrapped: x -->\n## Curated summary\n(no information yet)\n"
    curated, bullets, leading = compaction._split_sections(body)
    assert curated == "(no information yet)"
    assert bullets == []


def test_split_sections_missing_curated():
    body = "## Observations log\n- 2026-06-02T14:23Z [explicit-remember]: x\n"
    curated, bullets, leading = compaction._split_sections(body)
    assert curated == ""
    assert len(bullets) == 1


def test_split_sections_ignores_non_bullet_lines_in_log():
    body = (
        "## Curated summary\nx\n"
        "## Observations log\n"
        "- 2026-06-02T14:23Z [src]: ok\n"
        "stray text that is not a bullet\n"
        "- 2026-06-02T14:24Z [src]: second\n"
    )
    _, bullets, _ = compaction._split_sections(body)
    assert len(bullets) == 2


def test_build_compacted_body_clears_log():
    out = compaction._build_compacted_body(
        leading_metadata="<!-- bootstrapped: x -->",
        new_curated="Sam habla español.\nAntiff es B2B.",
    )
    curated, bullets, leading = compaction._split_sections(out)
    assert "Sam habla español" in curated
    assert "Antiff es B2B" in curated
    assert bullets == []
    assert "<!-- bootstrapped: x -->" in leading
    assert "<!-- last_compacted:" in leading or "<!-- last_compacted:" in curated or "<!-- last_compacted:" in out


def test_is_eligible_size():
    body = "## Curated summary\n" + ("x" * (compaction.BODY_SIZE_THRESHOLD + 100)) + "\n## Observations log\n"
    eligible, reason = compaction._is_eligible(body)
    assert eligible is True
    assert reason.startswith("body_size:")


def test_is_eligible_observation_count():
    bullets = "\n".join(
        f"- 2026-06-02T14:{i:02d}Z [explicit-remember]: fact{i}"
        for i in range(compaction.OBSERVATIONS_THRESHOLD + 1)
    )
    body = f"## Curated summary\nshort\n## Observations log\n{bullets}\n"
    eligible, reason = compaction._is_eligible(body)
    assert eligible is True
    assert reason.startswith("observations:")


def test_is_eligible_below_thresholds():
    body = (
        "## Curated summary\nshort\n"
        "## Observations log\n"
        "- 2026-06-02T14:00Z [explicit-remember]: only one\n"
    )
    eligible, _ = compaction._is_eligible(body)
    assert eligible is False


# --------------------------------------------------------------------------- #
# Integration: compact_skill
# --------------------------------------------------------------------------- #


pytestmark_integration = pytest.mark.integration


async def _fill_log_above_threshold(workspace_id: uuid.UUID, skill_name: str) -> int:
    """Append more observations than OBSERVATIONS_THRESHOLD so the skill
    becomes eligible. Returns the count appended."""
    from app.memory import append

    count = compaction.OBSERVATIONS_THRESHOLD + 2
    for i in range(count):
        await append.append_observation(
            workspace_id,
            skill_name,
            text=f"observación número {i}",
            source="explicit-remember",
        )
    return count


@pytest.mark.integration
@pytest.mark.asyncio
async def test_compact_skill_rewrites_curated_and_clears_log(
    fake_r2, db_session, workspace, monkeypatch
):
    await seed.ensure_company_skill(workspace.id)
    await _fill_log_above_threshold(workspace.id, COMPANY_SLUG)

    # Stub the model so the test doesn't hit LiteLLM. The "curated summary"
    # the stub returns is deterministic so the test can assert on it.
    async def _fake_model(curated, bullets):
        return "Antiff es B2B chargebacks. Equipo 100% remoto."

    monkeypatch.setattr(compaction, "_call_compaction_model", _fake_model)

    ok = await compaction.compact_skill(workspace.id, COMPANY_SLUG)
    assert ok is True

    # The log is empty post-compaction, the curated reflects the stub output.
    from app.skills import storage as skill_storage

    async with get_session() as session:
        skill = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace.id,
                    Skill.name == COMPANY_SLUG,
                )
            )
        ).scalar_one()
    body = await skill_storage.download_skill_body(
        workspace_id=skill.workspace_id,
        skill_id=skill.id,
        version=skill.version,
        r2_ref=skill.body_r2_ref,
    )
    curated, bullets, leading = compaction._split_sections(body)
    assert "Antiff es B2B chargebacks" in curated
    assert bullets == []
    assert "last_compacted" in body


@pytest.mark.integration
@pytest.mark.asyncio
async def test_compact_skill_noop_when_below_threshold(
    fake_r2, db_session, workspace, monkeypatch
):
    await seed.ensure_company_skill(workspace.id)
    # Only one observation, body is tiny. Should return False without
    # calling the model.
    from app.memory import append

    await append.append_observation(
        workspace.id, COMPANY_SLUG, text="solo uno", source="explicit-remember"
    )

    called = {"n": 0}

    async def _fake_model(curated, bullets):
        called["n"] += 1
        return "anything"

    monkeypatch.setattr(compaction, "_call_compaction_model", _fake_model)
    ok = await compaction.compact_skill(workspace.id, COMPANY_SLUG)
    assert ok is False
    assert called["n"] == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_compact_skill_skips_when_concurrent_append_bumps_version(
    fake_r2, db_session, workspace, monkeypatch
):
    """Race scenario: while the model call is in-flight, an append lands
    and bumps skill.version. The compaction write should detect the
    mismatch and skip rather than clobber the new observation."""
    await seed.ensure_company_skill(workspace.id)
    await _fill_log_above_threshold(workspace.id, COMPANY_SLUG)

    from app.memory import append

    async def _fake_model_then_append(curated, bullets):
        # Simulate a concurrent append landing while we're "calling" haiku.
        await append.append_observation(
            workspace.id,
            COMPANY_SLUG,
            text="racing append",
            source="explicit-remember",
        )
        return "fake new curated"

    monkeypatch.setattr(compaction, "_call_compaction_model", _fake_model_then_append)

    ok = await compaction.compact_skill(workspace.id, COMPANY_SLUG)
    assert ok is False  # version conflict -> skip

    from app.skills import storage as skill_storage

    async with get_session() as session:
        skill = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace.id,
                    Skill.name == COMPANY_SLUG,
                )
            )
        ).scalar_one()
    body = await skill_storage.download_skill_body(
        workspace_id=skill.workspace_id,
        skill_id=skill.id,
        version=skill.version,
        r2_ref=skill.body_r2_ref,
    )
    # The racing append is preserved; the curated was NOT overwritten.
    assert "racing append" in body
    assert "fake new curated" not in body


@pytest.mark.integration
@pytest.mark.asyncio
async def test_compact_skill_missing_returns_false(fake_r2, db_session, workspace):
    ok = await compaction.compact_skill(workspace.id, "does-not-exist")
    assert ok is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_compact_workspace_walks_all_memory_skills(
    fake_r2, db_session, workspace, user_a, monkeypatch
):
    await seed.ensure_company_skill(workspace.id)
    await seed.ensure_team_skill(workspace.id)
    await seed.ensure_user_skill(workspace.id, user_a.id, user_a.slack_user_id)

    # Make only company eligible -- team and user-skill stay under threshold.
    await _fill_log_above_threshold(workspace.id, COMPANY_SLUG)

    seen: list[str] = []

    async def _fake_model(curated, bullets):
        # We can't see which skill we're compacting from inside, but the
        # caller already filtered by eligibility -- so the fact that this
        # runs at all means an eligible skill went through.
        seen.append("called")
        return "merged"

    monkeypatch.setattr(compaction, "_call_compaction_model", _fake_model)

    counts = await compaction.compact_workspace(workspace.id)
    assert counts["examined"] == 3
    assert counts["compacted"] == 1
    assert len(seen) == 1
