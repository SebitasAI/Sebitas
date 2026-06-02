"""Unit tests for automations that don't touch Postgres.

Covers the pure-Python pieces:
- `router._filter_matches`: empty filter wildcards; key-by-key subset
  match; missing key + value mismatch reject.
- `actions.SafeDict` + `_render`: unknown keys stay as `{key}` literals;
  known keys interpolate; non-string event values stringify.
- `events.current_fire_depth` / `set_fire_depth` / `reset_fire_depth`:
  default 0, set returns token, reset restores prior value.
- `repository._validate_filter` / `_validate_action_config`: accept
  valid shapes, reject invalid ones with AutomationValidationError.
- Loop guard short-circuit: an Event with `fire_depth > MAX_FIRE_DEPTH`
  is dropped (`_find_matching` is not even consulted -- proven by
  monkeypatching it to raise).

Integration tests (DB + CRUD + permissions) live in
`test_automations_integration.py` and require TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.automations import repository as repo
from app.automations.actions import SafeDict, _render
from app.automations.events import (
    Event,
    current_fire_depth,
    reset_fire_depth,
    set_fire_depth,
)
from app.automations.router import MAX_FIRE_DEPTH, _filter_matches


# --------------------------------------------------------------------------- #
# Filter matching
# --------------------------------------------------------------------------- #


class TestFilterMatches:
    def test_empty_filter_is_wildcard(self):
        assert _filter_matches({}, {}) is True
        assert _filter_matches({}, {"anything": "goes"}) is True

    def test_key_must_be_present(self):
        assert _filter_matches({"app": "metabase"}, {"tool": "run_action"}) is False

    def test_value_must_equal(self):
        assert _filter_matches({"app": "metabase"}, {"app": "salesforce"}) is False

    def test_subset_match(self):
        assert (
            _filter_matches(
                {"app": "metabase"},
                {"app": "metabase", "tool": "run_action", "extra": "stuff"},
            )
            is True
        )

    def test_int_and_bool_primitives(self):
        assert _filter_matches({"count": 3}, {"count": 3}) is True
        assert _filter_matches({"ok": False}, {"ok": False}) is True
        assert _filter_matches({"ok": False}, {"ok": True}) is False


# --------------------------------------------------------------------------- #
# Template engine
# --------------------------------------------------------------------------- #


class TestSafeDictRender:
    def test_known_keys_interpolate(self):
        ev = Event(
            type="tool_failed",
            workspace_id=uuid.uuid4(),
            data={"tool_name": "run_action", "error": "boom"},
        )
        out = _render("Tool {tool_name} failed: {error}", ev)
        assert out == "Tool run_action failed: boom"

    def test_unknown_keys_stay_literal(self):
        ev = Event(type="tool_failed", workspace_id=uuid.uuid4(), data={"a": "1"})
        out = _render("a={a} missing={missing}", ev)
        assert out == "a=1 missing={missing}"

    def test_standard_fields_available(self):
        ev = Event(type="agent_error", workspace_id=uuid.uuid4(), data={})
        out = _render("type={type}", ev)
        assert out == "type=agent_error"

    def test_non_string_values_stringify(self):
        ev = Event(
            type="x",
            workspace_id=uuid.uuid4(),
            data={"count": 5, "ok": True},
        )
        # SafeDict gets values pre-stringified by `_render`; check the
        # full pipeline.
        out = _render("count={count} ok={ok}", ev)
        assert out == "count=5 ok=True"

    def test_safedict_missing_directly(self):
        sd = SafeDict({"present": "yes"})
        s = "{present} and {absent}".format_map(sd)
        assert s == "yes and {absent}"


# --------------------------------------------------------------------------- #
# Fire-depth contextvar
# --------------------------------------------------------------------------- #


class TestFireDepth:
    def test_default_zero(self):
        assert current_fire_depth() == 0

    def test_set_and_reset(self):
        token = set_fire_depth(2)
        try:
            assert current_fire_depth() == 2
        finally:
            reset_fire_depth(token)
        assert current_fire_depth() == 0

    def test_nested_set(self):
        outer = set_fire_depth(1)
        inner = set_fire_depth(2)
        try:
            assert current_fire_depth() == 2
        finally:
            reset_fire_depth(inner)
        assert current_fire_depth() == 1
        reset_fire_depth(outer)
        assert current_fire_depth() == 0


# --------------------------------------------------------------------------- #
# Validators
# --------------------------------------------------------------------------- #


class TestValidators:
    def test_filter_none_becomes_empty(self):
        assert repo._validate_filter(None) == {}

    def test_filter_rejects_non_dict(self):
        with pytest.raises(repo.AutomationValidationError):
            repo._validate_filter(["a", "b"])  # type: ignore[arg-type]

    def test_filter_rejects_non_string_key(self):
        with pytest.raises(repo.AutomationValidationError):
            repo._validate_filter({1: "v"})  # type: ignore[dict-item]

    def test_filter_rejects_nested_dict_value(self):
        with pytest.raises(repo.AutomationValidationError):
            repo._validate_filter({"k": {"nested": "no"}})

    def test_filter_accepts_primitive_values(self):
        out = repo._validate_filter({"s": "x", "n": 3, "b": True, "f": 1.5, "z": None})
        assert out == {"s": "x", "n": 3, "b": True, "f": 1.5, "z": None}

    def test_slack_notify_requires_text(self):
        with pytest.raises(repo.AutomationValidationError):
            repo._validate_action_config("slack_notify", {"text": ""})
        with pytest.raises(repo.AutomationValidationError):
            repo._validate_action_config("slack_notify", {"channel": "C1"})

    def test_agent_run_requires_prompt(self):
        with pytest.raises(repo.AutomationValidationError):
            repo._validate_action_config("agent_run", {"prompt": "   "})

    def test_unknown_action_type_rejected(self):
        with pytest.raises(repo.AutomationValidationError):
            repo._validate_action_config("send_pigeon", {"prompt": "x"})

    def test_channel_must_be_string_or_none(self):
        with pytest.raises(repo.AutomationValidationError):
            repo._validate_action_config(
                "slack_notify", {"text": "hi", "channel": 123}
            )
        repo._validate_action_config("slack_notify", {"text": "hi", "channel": None})
        repo._validate_action_config("slack_notify", {"text": "hi", "channel": "C123"})


# --------------------------------------------------------------------------- #
# Loop guard short-circuit
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_loop_guard_drops_over_depth(monkeypatch):
    """When `event.fire_depth > MAX_FIRE_DEPTH`, the router must drop the
    event WITHOUT calling `_find_matching` (which would touch the DB).
    Proven by replacing `_find_matching` with a sentinel that raises."""
    from app.automations import router as router_module

    async def _boom(_ev):
        raise AssertionError("_find_matching should not be called")

    monkeypatch.setattr(router_module, "_find_matching", _boom)

    ev = Event(
        type="agent_error",
        workspace_id=uuid.uuid4(),
        data={},
        fire_depth=MAX_FIRE_DEPTH + 1,
    )
    # Should return silently without raising.
    await router_module.route(ev)


@pytest.mark.asyncio
async def test_loop_guard_allows_at_max_depth(monkeypatch):
    """Depth exactly at MAX_FIRE_DEPTH is still allowed; we only drop ABOVE."""
    from app.automations import router as router_module

    called = {"n": 0}

    async def _empty(_ev):
        called["n"] += 1
        return []

    monkeypatch.setattr(router_module, "_find_matching", _empty)

    ev = Event(
        type="agent_error",
        workspace_id=uuid.uuid4(),
        data={},
        fire_depth=MAX_FIRE_DEPTH,
    )
    await router_module.route(ev)
    assert called["n"] == 1
