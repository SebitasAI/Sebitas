"""Tests for the param-aware auto-improve prompt + extractor.

After the Gong / `includeParties=false` incident, the auto-improve was
rewritten to receive the ACTUAL PARAMS the agent passed to each action
(not just action_ids). This test pins:

  - `_extract_insight` accepts `action_calls` (list of dicts with params)
  - The prompt blob includes params per action
  - `maybe_improve_skill` passes params through from `integration_calls`
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from app.integrations import auto_improve


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_extract_insight_receives_param_details(monkeypatch):
    """The prompt sent to haiku must include the literal param values
    the agent passed -- otherwise haiku can't spot a wrong flag."""
    captured: dict = {}

    class _Msg:
        def __init__(self, c):
            self.content = c

    class _Choice:
        def __init__(self, c):
            self.message = _Msg(c)

    class _Usage:
        prompt_tokens = 10
        completion_tokens = 5

    class _Resp:
        def __init__(self, c):
            self.choices = [_Choice(c)]
            self.usage = _Usage()

    async def _fake_acompletion(model, messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return _Resp('{"has_insight": false}')

    monkeypatch.setattr(auto_improve.litellm, "acompletion", _fake_acompletion)

    await auto_improve._extract_insight(
        "buscar calls con MercadoLibre",
        "no encontré",
        "gong",
        [
            {
                "action_id": "gong-get-extensive-data",
                "params": {
                    "includeParties": False,
                    "fromDateTime": "2026-05-01T00:00:00Z",
                    "maxResults": 500,
                },
            },
        ],
    )

    prompt = captured["prompt"]
    # The specific param the agent set wrong should be in the prompt.
    assert "includeParties=False" in prompt
    assert "gong-get-extensive-data" in prompt
    # The new prompt structure mentions PARAM ERROR explicitly.
    assert "PARAM ERROR" in prompt


@pytest.mark.asyncio
async def test_extract_insight_returns_none_when_has_insight_false(monkeypatch):
    class _Msg:
        def __init__(self, c):
            self.content = c

    class _Choice:
        def __init__(self, c):
            self.message = _Msg(c)

    class _Usage:
        prompt_tokens = 10
        completion_tokens = 5

    class _Resp:
        def __init__(self, c):
            self.choices = [_Choice(c)]
            self.usage = _Usage()

    async def _fake_acompletion(model, messages, **kwargs):
        return _Resp('{"has_insight": false}')

    monkeypatch.setattr(auto_improve.litellm, "acompletion", _fake_acompletion)

    out = await auto_improve._extract_insight(
        "x", "y", "gong",
        [{"action_id": "gong-list-calls", "params": {}}],
    )
    assert out is None


@pytest.mark.asyncio
async def test_extract_insight_returns_string_when_emitted(monkeypatch):
    class _Msg:
        def __init__(self, c):
            self.content = c

    class _Choice:
        def __init__(self, c):
            self.message = _Msg(c)

    class _Usage:
        prompt_tokens = 10
        completion_tokens = 5

    class _Resp:
        def __init__(self, c):
            self.choices = [_Choice(c)]
            self.usage = _Usage()

    async def _fake_acompletion(model, messages, **kwargs):
        return _Resp(
            '{"has_insight": true, "insight": '
            '"Cuando filtres calls por nombre de empresa, set '
            'includeParties=true en gong-get-extensive-data."}'
        )

    monkeypatch.setattr(auto_improve.litellm, "acompletion", _fake_acompletion)

    out = await auto_improve._extract_insight(
        "x", "y", "gong",
        [{"action_id": "gong-get-extensive-data",
          "params": {"includeParties": False}}],
    )
    assert out is not None
    assert "includeParties=true" in out


@pytest.mark.asyncio
async def test_maybe_improve_passes_params_through(
    fake_r2, db_session, workspace, monkeypatch
):
    """End-to-end-ish: `maybe_improve_skill` should hand the params
    through to `_extract_insight` and not just the action_ids."""
    from app.integrations import catalog_skills
    from app.skills import registry as skill_registry

    body = "## Available actions\n- gong-x\n\n## Usage notes\n"
    await skill_registry.create_skill(
        workspace_id=workspace.id,
        name="integrations/gong",
        description="x",
        activation_default="on_demand",
        body=body,
        links=[],
        size_bytes=len(body.encode("utf-8")),
        created_by_user_id=None,
        source="catalog",
        scope="workspace",
    )

    captured_calls: list[list[dict]] = []

    async def _fake_extract(user_text, agent_response, app, action_calls):
        captured_calls.append(action_calls)
        return None

    monkeypatch.setattr(auto_improve, "_extract_insight", _fake_extract)

    await auto_improve.maybe_improve_skill(
        workspace_id=workspace.id,
        user_text="x",
        agent_response="y",
        integration_calls=[
            {
                "app": "gong",
                "action_id": "gong-get-extensive-data",
                "params": {"includeParties": False, "maxResults": 500},
            },
        ],
    )

    assert captured_calls == [
        [{"action_id": "gong-get-extensive-data",
          "params": {"includeParties": False, "maxResults": 500}}]
    ]
