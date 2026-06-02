"""Tests for the auto-generated integration skills + auto-improve.

The Pipedream catalog is stubbed via monkeypatch so we don't hit the
real API. Tests cover:

  - render of `## Available actions` section from a stub Pipedream catalog
  - upsert creates a new Skill row with source='catalog'
  - upsert PRESERVES the `## Usage notes` section across refreshes
  - delete_integration_skill drops the row
  - auto_improve appends a bullet to Usage notes (capped at MAX)
  - auto_improve dedups same-text insights
  - `_extract_integration_calls` parses run_messages correctly
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.agent.runner import _extract_integration_calls
from app.db.models import Skill
from app.db.session import get_session
from app.integrations import auto_improve, catalog_skills


pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Stub Pipedream
# --------------------------------------------------------------------------- #


def _stub_pd(monkeypatch, actions: list[dict], component_props: dict[str, list]):
    """Replace pipedream.search_actions + get_component for the duration
    of a test. Also stubs `_generate_action_usage_hint` to return ""
    so the haiku call doesn't fire against live LiteLLM during tests."""
    from app.integrations import pipedream as pd

    async def _search(app, query=None):
        return actions

    async def _get_comp(component_id):
        return {"configurable_props": component_props.get(component_id, [])}

    async def _no_hint(action):
        return ""

    monkeypatch.setattr(pd, "search_actions", _search)
    monkeypatch.setattr(pd, "get_component", _get_comp)
    monkeypatch.setattr(catalog_skills, "_generate_action_usage_hint", _no_hint)
    # Bust the in-process catalog cache so the test sees fresh data.
    catalog_skills._catalog_cache.clear()
    catalog_skills._action_hint_cache.clear()


@pytest.mark.asyncio
async def test_upsert_creates_skill_with_catalog(
    fake_r2, db_session, workspace, monkeypatch
):
    _stub_pd(
        monkeypatch,
        actions=[
            {"key": "gong-list-calls", "name": "List Calls",
             "description": "List calls. Date filters only."},
            {"key": "gong-get-extensive", "name": "Get Extensive",
             "description": "Detailed call data with parties."},
        ],
        component_props={
            "gong-list-calls": [
                {"name": "fromDateTime", "type": "string", "optional": True,
                 "description": "ISO 8601 start time."},
            ],
            "gong-get-extensive": [
                {"name": "callIds", "type": "string[]", "optional": True,
                 "description": "List of call ids."},
                {"name": "includeParties", "type": "boolean", "optional": True,
                 "description": "Include party info."},
            ],
        },
    )

    new_id = await catalog_skills.upsert_integration_skill(workspace.id, "gong")
    assert new_id is not None

    async with get_session() as s:
        skill = (
            await s.execute(select(Skill).where(Skill.id == new_id))
        ).scalar_one()
    assert skill.name == "integrations/gong"
    assert skill.source == "catalog"
    assert skill.activation_default == "on_demand"

    from app.skills import storage as skill_storage

    body = await skill_storage.download_skill_body(
        workspace_id=skill.workspace_id, skill_id=skill.id,
        version=skill.version, r2_ref=skill.body_r2_ref,
    )
    assert catalog_skills.SECTION_AVAILABLE in body
    assert catalog_skills.SECTION_USAGE in body
    assert "gong-list-calls" in body
    assert "gong-get-extensive" in body
    assert "includeParties" in body


@pytest.mark.asyncio
async def test_upsert_preserves_usage_notes(
    fake_r2, db_session, workspace, monkeypatch
):
    _stub_pd(
        monkeypatch,
        actions=[{"key": "gong-x", "description": "x"}],
        component_props={"gong-x": []},
    )
    await catalog_skills.upsert_integration_skill(workspace.id, "gong")

    # Inject an admin-style usage note.
    async with get_session() as s:
        skill = (
            await s.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace.id,
                    Skill.name == "integrations/gong",
                )
            )
        ).scalar_one()
    from app.skills import storage as skill_storage, registry as skill_registry

    old_body = await skill_storage.download_skill_body(
        workspace_id=skill.workspace_id, skill_id=skill.id,
        version=skill.version, r2_ref=skill.body_r2_ref,
    )
    edited = old_body + "\n- 2026-06-01 [admin]: usá X para Y.\n"
    await skill_registry.update_skill_body(
        skill_id=skill.id, new_body=edited, new_size_bytes=len(edited.encode("utf-8"))
    )

    # Change the action catalog + re-upsert.
    _stub_pd(
        monkeypatch,
        actions=[{"key": "gong-x", "description": "x"},
                 {"key": "gong-y", "description": "y"}],
        component_props={"gong-x": [], "gong-y": []},
    )
    await catalog_skills.upsert_integration_skill(workspace.id, "gong")

    async with get_session() as s:
        skill = (
            await s.execute(
                select(Skill).where(Skill.id == skill.id)
            )
        ).scalar_one()
    new_body = await skill_storage.download_skill_body(
        workspace_id=skill.workspace_id, skill_id=skill.id,
        version=skill.version, r2_ref=skill.body_r2_ref,
    )
    assert "gong-y" in new_body  # refresh worked
    assert "2026-06-01 [admin]: usá X para Y" in new_body  # preserved


@pytest.mark.asyncio
async def test_delete_drops_skill(fake_r2, db_session, workspace, monkeypatch):
    _stub_pd(
        monkeypatch,
        actions=[{"key": "x", "description": "x"}],
        component_props={"x": []},
    )
    await catalog_skills.upsert_integration_skill(workspace.id, "gong")
    ok = await catalog_skills.delete_integration_skill(workspace.id, "gong")
    assert ok is True
    async with get_session() as s:
        row = (
            await s.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace.id,
                    Skill.name == "integrations/gong",
                )
            )
        ).scalar_one_or_none()
    assert row is None


# --------------------------------------------------------------------------- #
# Auto-improve
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_auto_improve_appends_when_insight(
    fake_r2, db_session, workspace, monkeypatch
):
    _stub_pd(
        monkeypatch,
        actions=[{"key": "gong-list-calls", "description": "x"}],
        component_props={"gong-list-calls": []},
    )
    await catalog_skills.upsert_integration_skill(workspace.id, "gong")

    async def _fake_insight(*args, **kwargs):
        return "Para filtrar calls por empresa, usá gong-get-extensive con includeParties=true."

    monkeypatch.setattr(auto_improve, "_extract_insight", _fake_insight)

    written = await auto_improve.maybe_improve_skill(
        workspace_id=workspace.id,
        user_text="trae calls con MercadoLibre",
        agent_response="No las encontré",
        integration_calls=[{"app": "gong", "action_id": "gong-list-calls"}],
    )
    assert written == 1

    async with get_session() as s:
        skill = (
            await s.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace.id,
                    Skill.name == "integrations/gong",
                )
            )
        ).scalar_one()
    from app.skills import storage as skill_storage

    body = await skill_storage.download_skill_body(
        workspace_id=skill.workspace_id, skill_id=skill.id,
        version=skill.version, r2_ref=skill.body_r2_ref,
    )
    assert "[auto-improve]" in body
    assert "gong-get-extensive" in body  # the insight text


@pytest.mark.asyncio
async def test_auto_improve_dedups_same_insight(
    fake_r2, db_session, workspace, monkeypatch
):
    _stub_pd(
        monkeypatch,
        actions=[{"key": "x", "description": "x"}],
        component_props={"x": []},
    )
    await catalog_skills.upsert_integration_skill(workspace.id, "gong")

    async def _same(*args, **kwargs):
        return "Usá Y, no X, cuando el filtro sea por nombre."

    monkeypatch.setattr(auto_improve, "_extract_insight", _same)

    first = await auto_improve.maybe_improve_skill(
        workspace_id=workspace.id, user_text="a", agent_response="b",
        integration_calls=[{"app": "gong", "action_id": "x"}],
    )
    second = await auto_improve.maybe_improve_skill(
        workspace_id=workspace.id, user_text="a", agent_response="b",
        integration_calls=[{"app": "gong", "action_id": "x"}],
    )
    assert first == 1
    assert second == 0  # dedup


@pytest.mark.asyncio
async def test_auto_improve_emits_nothing_when_no_insight(
    fake_r2, db_session, workspace, monkeypatch
):
    _stub_pd(
        monkeypatch,
        actions=[{"key": "x", "description": "x"}],
        component_props={"x": []},
    )
    await catalog_skills.upsert_integration_skill(workspace.id, "gong")

    async def _none(*args, **kwargs):
        return None  # haiku said no insight

    monkeypatch.setattr(auto_improve, "_extract_insight", _none)

    n = await auto_improve.maybe_improve_skill(
        workspace_id=workspace.id, user_text="a", agent_response="b",
        integration_calls=[{"app": "gong", "action_id": "x"}],
    )
    assert n == 0


# --------------------------------------------------------------------------- #
# Run-messages extractor
# --------------------------------------------------------------------------- #


def test_extract_integration_calls_finds_run_action():
    run_messages = [
        {"role": "user", "content": "Trae calls de Gong"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Llamando..."},
                {
                    "type": "tool_use",
                    "id": "abc",
                    "name": "run_action",
                    "input": {"app": "gong", "action_id": "gong-list-calls", "configured": {}},
                },
            ],
        },
        {"role": "tool", "content": "result"},
    ]
    out = _extract_integration_calls(run_messages)
    assert out == [{
        "app": "gong", "action_id": "gong-list-calls",
        "params": {}, "filter_substring": None,
    }]


def test_extract_integration_calls_ignores_other_tools():
    run_messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "load_skill", "input": {"name": "x"}},
                {"type": "tool_use", "name": "run_action", "input": {"app": "gong", "action_id": "y"}},
                {"type": "tool_use", "name": "calc", "input": {"expression": "2+2"}},
            ],
        }
    ]
    out = _extract_integration_calls(run_messages)
    assert out == [{
        "app": "gong", "action_id": "y",
        "params": {}, "filter_substring": None,
    }]


def test_extract_integration_calls_empty_when_no_tool_calls():
    run_messages = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "hola back"},
    ]
    out = _extract_integration_calls(run_messages)
    assert out == []
