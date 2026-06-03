"""Defensive behavior of the run_code tool wrapper.

Observed bug (Antiff trace 609ae11250e0): LLM called `run_code` with
`input={}` and the handler raised `TypeError: missing 'code'`. The
error string went back to the LLM which immediately retried with the
same empty input, burning iterations until the cap. These tests pin
the new wrapper behavior so a regression doesn't reintroduce the loop.
"""

from __future__ import annotations

import asyncio

from app.agent.tools import _run_code


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if not asyncio.get_event_loop().is_running() else asyncio.run(coro)


class TestRunCodeWrapper:
    def test_no_args_returns_clear_error(self):
        # Empty call -- mirrors the LLM emitting an empty tool_use input.
        result = asyncio.run(_run_code())
        assert "código Python" in result or "code" in result.lower()
        # Does NOT raise TypeError.
        assert "TypeError" not in result

    def test_empty_string_returns_clear_error(self):
        result = asyncio.run(_run_code(code=""))
        assert "código" in result.lower() or "code" in result.lower()
        assert "vacío" in result.lower() or "empty" in result.lower()

    def test_whitespace_only_returns_clear_error(self):
        result = asyncio.run(_run_code(code="   \n\t  "))
        assert "vacío" in result.lower() or "empty" in result.lower()

    def test_unknown_kwargs_swallowed(self):
        # Some LLM tool_use blocks include fields the schema doesn't
        # declare. The wrapper must accept extra kwargs without crashing
        # -- the sandbox call only sees the validated `code` arg.
        # We pass code=None alongside the unknown kwarg so the wrapper
        # short-circuits before hitting the sandbox; what we're testing
        # is "no TypeError on unknown kwargs", not the sandbox path.
        result = asyncio.run(_run_code(code=None, extra="surprise", another=42))
        assert "TypeError" not in result
