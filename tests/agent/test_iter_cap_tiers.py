"""Unit tests for the 3 iteration-cap protection tiers in graph.py.

Tier 1: wrap-up hint when iters close to cap
Tier 2: loop detection (same tool+input N times in a row)
Tier 3: project-mode cap via contextvar

These tests don't touch Postgres / Slack / Langfuse / Anthropic. They
exercise the pure-Python helpers and the routing decisions around
iter caps."""

from __future__ import annotations

from app.agent.context import agent_max_iter_var
from app.agent.graph import (
    LOOP_REPETITION_LIMIT,
    WRAP_UP_THRESHOLD,
    _effective_cap,
    _recent_repeat_count,
    _route_after_agent,
    _signature_of,
    _wrap_up_hint,
)


# --------------------------------------------------------------------------- #
# _signature_of: canonical (tool, input) key
# --------------------------------------------------------------------------- #


class TestSignature:
    def test_identical_calls_match(self):
        a = {"id": "x1", "name": "run_action", "input": {"app": "salesforce", "action": "Lead.find"}}
        b = {"id": "x2", "name": "run_action", "input": {"action": "Lead.find", "app": "salesforce"}}
        # Key order shouldn't matter; signature stays the same.
        assert _signature_of(a) == _signature_of(b)

    def test_different_tools_different_signatures(self):
        a = {"id": "x1", "name": "run_code", "input": {}}
        b = {"id": "x2", "name": "run_action", "input": {}}
        assert _signature_of(a) != _signature_of(b)

    def test_different_input_different_signature(self):
        a = {"id": "x1", "name": "run_action", "input": {"app": "a"}}
        b = {"id": "x2", "name": "run_action", "input": {"app": "b"}}
        assert _signature_of(a) != _signature_of(b)

    def test_handles_missing_input(self):
        a = {"id": "x1", "name": "calc"}
        # No exception; signature is well-defined.
        assert _signature_of(a)

    def test_handles_non_jsonable_input(self):
        # Defensive: if some non-JSON object sneaks in, the function must
        # not crash (str() fallback).
        a = {"id": "x1", "name": "calc", "input": {"obj": object()}}
        assert _signature_of(a)


# --------------------------------------------------------------------------- #
# _recent_repeat_count: how many of the last N turns used this signature
# --------------------------------------------------------------------------- #


def _assistant_with(*tool_uses) -> dict:
    """Helper: build an assistant turn with the given tool_use blocks."""
    return {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": tu["id"], "name": tu["name"], "input": tu.get("input") or {}}
            for tu in tool_uses
        ],
    }


def _tool_result(tu_id: str = "x") -> dict:
    return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tu_id, "content": "ok"}]}


class TestRecentRepeatCount:
    def test_under_threshold_returns_zero(self):
        # Only 2 assistant turns -- can't have hit LOOP_REPETITION_LIMIT (3).
        messages = [
            _assistant_with({"id": "1", "name": "calc", "input": {"e": "1"}}),
            _tool_result("1"),
            _assistant_with({"id": "2", "name": "calc", "input": {"e": "1"}}),
        ]
        sig = _signature_of({"name": "calc", "input": {"e": "1"}})
        assert _recent_repeat_count(messages, sig) == 0

    def test_repeated_call_in_each_of_last_3_turns_hits_limit(self):
        messages = []
        for i in range(LOOP_REPETITION_LIMIT):
            messages.append(_assistant_with({"id": str(i), "name": "calc", "input": {"e": "1"}}))
            messages.append(_tool_result(str(i)))
        sig = _signature_of({"name": "calc", "input": {"e": "1"}})
        assert _recent_repeat_count(messages, sig) == LOOP_REPETITION_LIMIT

    def test_different_input_does_not_count(self):
        messages = []
        for i, val in enumerate(["1", "2", "3"]):
            messages.append(_assistant_with({"id": str(i), "name": "calc", "input": {"e": val}}))
            messages.append(_tool_result(str(i)))
        sig = _signature_of({"name": "calc", "input": {"e": "1"}})
        # Only 1 of the 3 turns matches the e='1' signature.
        assert _recent_repeat_count(messages, sig) == 1

    def test_multi_tool_turn_matches_if_any_block_matches(self):
        # A turn that issues multiple tool_uses counts if ANY of them matches.
        messages = []
        for i in range(LOOP_REPETITION_LIMIT):
            messages.append(_assistant_with(
                {"id": f"a{i}", "name": "calc", "input": {"e": "1"}},
                {"id": f"b{i}", "name": "get_current_time", "input": {}},
            ))
            messages.append(_tool_result(f"a{i}"))
        sig = _signature_of({"name": "calc", "input": {"e": "1"}})
        assert _recent_repeat_count(messages, sig) == LOOP_REPETITION_LIMIT


# --------------------------------------------------------------------------- #
# _wrap_up_hint: structure of the injected nudge
# --------------------------------------------------------------------------- #


class TestWrapUpHint:
    def test_hint_is_user_role(self):
        h = _wrap_up_hint(3)
        assert h["role"] == "user"

    def test_hint_text_mentions_remaining(self):
        h = _wrap_up_hint(2)
        text = h["content"][0]["text"]
        assert "2" in text
        assert "iteraciones" in text.lower()

    def test_hint_warns_against_ending_on_tool_use(self):
        h = _wrap_up_hint(1)
        text = h["content"][0]["text"].lower()
        assert "tool" in text
        # Explicitly warns against terminating with a tool_use block.
        assert "tool_use" in text


# --------------------------------------------------------------------------- #
# _effective_cap: contextvar override + fallback
# --------------------------------------------------------------------------- #


class TestEffectiveCap:
    def test_no_override_uses_settings(self):
        token = agent_max_iter_var.set(0)
        try:
            from app.config import get_settings

            assert _effective_cap() == get_settings().agent_max_iterations
        finally:
            agent_max_iter_var.reset(token)

    def test_positive_override_wins(self):
        token = agent_max_iter_var.set(99)
        try:
            assert _effective_cap() == 99
        finally:
            agent_max_iter_var.reset(token)

    def test_zero_override_falls_back(self):
        # 0 sentinel must NOT be used as the cap (would terminate before start).
        # Falls back to the settings value.
        token = agent_max_iter_var.set(0)
        try:
            from app.config import get_settings

            assert _effective_cap() == get_settings().agent_max_iterations
        finally:
            agent_max_iter_var.reset(token)


# --------------------------------------------------------------------------- #
# _route_after_agent: respects per-run cap
# --------------------------------------------------------------------------- #


class TestRouteAfterAgent:
    def test_returns_tools_when_under_cap_and_has_tool_use(self):
        state = {
            "messages": [_assistant_with({"id": "1", "name": "calc", "input": {}})],
            "iterations": 5,
        }
        token = agent_max_iter_var.set(35)
        try:
            assert _route_after_agent(state) == "tools"
        finally:
            agent_max_iter_var.reset(token)

    def test_returns_end_when_at_cap(self):
        state = {
            "messages": [_assistant_with({"id": "1", "name": "calc", "input": {}})],
            "iterations": 35,
        }
        token = agent_max_iter_var.set(35)
        try:
            # `_route_after_agent` returns END (a langgraph constant) when
            # the cap is hit; we just need to assert it's NOT "tools".
            assert _route_after_agent(state) != "tools"
        finally:
            agent_max_iter_var.reset(token)

    def test_higher_cap_lets_more_iters_through(self):
        # Same state, but project-mode cap of 60 -- 35 iters still inside.
        state = {
            "messages": [_assistant_with({"id": "1", "name": "calc", "input": {}})],
            "iterations": 35,
        }
        token = agent_max_iter_var.set(60)
        try:
            assert _route_after_agent(state) == "tools"
        finally:
            agent_max_iter_var.reset(token)


# --------------------------------------------------------------------------- #
# Project-mode detection (Tier 3, helper lives in runner.py)
# --------------------------------------------------------------------------- #


class TestProjectModeDetection:
    def test_short_prompt_no_override(self):
        from app.agent.runner import _detect_project_iter_cap

        assert _detect_project_iter_cap("Resume este thread") == 0

    def test_long_but_no_keywords_no_override(self):
        from app.agent.runner import _detect_project_iter_cap

        text = "x" * 500  # long, but no project keywords
        assert _detect_project_iter_cap(text) == 0

    def test_long_with_competitive_analysis_keyword_overrides(self):
        from app.agent.runner import _detect_project_iter_cap
        from app.config import get_settings

        text = (
            "Necesito un competitive analysis de nosotros vs Notion AI, Glean "
            "y Moveworks. Pricing, features, positioning. Hacelo en PDF de "
            "12 páginas para presentar al board, incluí gráficos comparativos "
            "y un executive summary al principio. Tiene que estar listo "
            "para el jueves."
        )
        assert _detect_project_iter_cap(text) == get_settings().agent_max_iterations_project

    def test_long_with_auditoria_keyword_overrides(self):
        from app.agent.runner import _detect_project_iter_cap

        text = (
            "Hacé una auditoría completa del pipeline comercial de Q2. Buscá "
            "deals abiertos en Salesforce, cruzá con tickets de Linear de "
            "cada cliente, y armá un reporte ejecutivo en PDF que cubra "
            "estado por cliente + risk score + next steps."
        )
        assert _detect_project_iter_cap(text) > 0

    def test_short_with_keyword_no_override(self):
        # Keyword present but prompt too short -- not a real project ask.
        from app.agent.runner import _detect_project_iter_cap

        assert _detect_project_iter_cap("hacé un análisis completo") == 0


# --------------------------------------------------------------------------- #
# Constants pinned (signals to future-Sam that they're load-bearing)
# --------------------------------------------------------------------------- #


def test_constants_pinned():
    assert WRAP_UP_THRESHOLD == 3
    assert LOOP_REPETITION_LIMIT == 3
