"""Unit tests for the deterministic iteration-loop detector in
`app.integrations.auto_improve._detect_iteration_loop`.

Pure function — no DB, no LLM. Covers:

  - fires when same action_id appears >= threshold times with no
    filter_substring on any call
  - does NOT fire when below threshold
  - does NOT fire when filter_substring was used on at least one of
    the duplicate calls (the agent already knows the right pattern)
  - returns the loop note pointing at the specific action_id, with
    `filter_substring` example included
"""

from __future__ import annotations

from app.integrations.auto_improve import (
    _ITERATION_LOOP_THRESHOLD,
    _detect_iteration_loop,
)


def _call(action_id: str, filter_substring: str | None = None) -> dict:
    return {
        "action_id": action_id,
        "params": {},
        "filter_substring": filter_substring,
    }


def test_fires_when_threshold_hit_without_filter():
    calls = [_call("gong-get-extensive-data")] * _ITERATION_LOOP_THRESHOLD
    insight = _detect_iteration_loop(calls)
    assert insight is not None
    assert "gong-get-extensive-data" in insight
    assert "filter_substring" in insight


def test_does_not_fire_below_threshold():
    calls = [_call("gong-get-extensive-data")] * (_ITERATION_LOOP_THRESHOLD - 1)
    assert _detect_iteration_loop(calls) is None


def test_does_not_fire_when_filter_used_on_any_iteration():
    # Even if the agent iterated 5 times, if one of those calls
    # passed filter_substring it already knows the pattern.
    calls = [
        _call("gong-get-extensive-data"),
        _call("gong-get-extensive-data"),
        _call("gong-get-extensive-data"),
        _call("gong-get-extensive-data", filter_substring="MercadoLibre"),
        _call("gong-get-extensive-data"),
    ]
    assert _detect_iteration_loop(calls) is None


def test_distinguishes_actions_correctly():
    # 2 calls of one action + 3 of another -> only the second loops.
    calls = [
        _call("gong-list-calls"),
        _call("gong-list-calls"),
        _call("gong-get-extensive-data"),
        _call("gong-get-extensive-data"),
        _call("gong-get-extensive-data"),
    ]
    insight = _detect_iteration_loop(calls)
    assert insight is not None
    assert "gong-get-extensive-data" in insight
    assert "gong-list-calls" not in insight


def test_handles_empty_input():
    assert _detect_iteration_loop([]) is None


def test_ignores_calls_with_no_action_id():
    calls = [
        {"params": {}, "filter_substring": None},
        _call("gong-x"),
        _call("gong-x"),
        _call("gong-x"),
    ]
    insight = _detect_iteration_loop(calls)
    assert insight is not None
    assert "gong-x" in insight


def test_blank_filter_substring_treated_as_off():
    # Empty / whitespace strings should NOT count as having used the filter.
    calls = [
        _call("gong-y", filter_substring=""),
        _call("gong-y", filter_substring="   "),
        _call("gong-y"),
    ]
    insight = _detect_iteration_loop(calls)
    assert insight is not None
    assert "gong-y" in insight


def test_insight_includes_concrete_example():
    calls = [_call("hubspot-list-tickets")] * 3
    insight = _detect_iteration_loop(calls)
    assert insight is not None
    # The note must show the agent how to actually use filter_substring,
    # not just that it exists.
    assert "run_action" in insight
    assert "filter_substring=" in insight
