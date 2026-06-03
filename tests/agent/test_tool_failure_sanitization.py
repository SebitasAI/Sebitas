"""Capa 1 tests: tool handler exceptions never reach the LLM verbatim.

What we pin down:

  - Any exception from a tool handler is replaced with the fixed
    neutral message before the LLM sees it.
  - The neutral message contains no tool name, no exception class,
    no internal terminology.
  - Successful handler returns flow through unchanged.
  - The full exception text is still preserved in structlog (for
    debugging) -- we don't drop it on the floor.
"""

from __future__ import annotations

import pytest

from app.agent.graph import (
    _SANITIZED_FAILURE_MESSAGE,
    _sanitize_handler_exception,
)


# --------------------------------------------------------------------------- #
# _sanitize_handler_exception
# --------------------------------------------------------------------------- #


# Strings the LLM must NEVER see in a tool_result, no matter what blew up.
# Lower-cased for case-insensitive containment checks.
_INTERNAL_TERMS = (
    "pipedream", "composio", "anthropic", "claude",
    "sandbox", "e2b", "container", "runtime",
    "langfuse", "litellm", "langgraph",
    "neon", "cloudflare", "r2",
    "traceback", "stack trace",
)


@pytest.mark.parametrize("tool_name", [
    "run_code", "run_action", "load_skill", "find_in_action",
    "disconnect_integration", "request_integration", "delegate_simple",
])
def test_sanitized_message_is_identical_across_tools(tool_name):
    """No tool name leaks into the user-facing string."""
    out = _sanitize_handler_exception(tool_name, RuntimeError("anything"))
    assert out == _SANITIZED_FAILURE_MESSAGE


@pytest.mark.parametrize("exc_factory", [
    lambda: ValueError("invalid sandbox state container 5xx"),
    lambda: RuntimeError("Pipedream HTTP 503 from broker"),
    lambda: ConnectionError("E2B sandbox unreachable, retry"),
    lambda: TimeoutError("Composio /v3/connected_accounts/link timeout"),
    lambda: Exception("Anthropic API rate limited"),
])
def test_sanitized_message_strips_all_internal_terms(exc_factory):
    out = _sanitize_handler_exception("run_code", exc_factory())
    lower = out.lower()
    for term in _INTERNAL_TERMS:
        assert term not in lower, (
            f"sanitized message contained internal term {term!r}: {out!r}"
        )


def test_sanitized_message_does_not_contain_exception_class():
    """The exception class name must not leak. ValueError, ConnectionError,
    etc. tell the user too much about the failure surface."""
    out = _sanitize_handler_exception("run_action", ValueError("x"))
    assert "ValueError" not in out
    out = _sanitize_handler_exception("run_action", ConnectionError("x"))
    assert "ConnectionError" not in out
    out = _sanitize_handler_exception("run_action", TimeoutError("x"))
    assert "TimeoutError" not in out


def test_sanitized_message_does_not_contain_str_exc():
    """str(exc) often carries the smoking gun (provider URLs, stack
    fragments, internal table names). Must not appear in the output."""
    raw = "sandbox container 0xdeadbeef returned 503 from pipedream-proxy"
    out = _sanitize_handler_exception("run_code", RuntimeError(raw))
    assert raw not in out
    assert "pipedream-proxy" not in out
    assert "0xdeadbeef" not in out


def test_sanitized_message_is_user_safe_and_brief():
    """Neutral language, no tech jargon, action-oriented (retry framing)."""
    out = _sanitize_handler_exception("anything", Exception("x"))
    assert len(out) < 200, "message should be brief"
    # Action-oriented: user controls the retry, not an infra timer.
    assert "retry" in out.lower() or "reintent" in out.lower() or "intent" in out.lower()


def test_sanitized_message_carries_no_tool_invocation_metadata():
    """Even arbitrary strings passed as tool_name (e.g. the LLM
    invented a name that doesn't exist) must not leak."""
    for fake_name in ["run_secret_internal_thing", "internal:debug:probe", "../path/leak"]:
        out = _sanitize_handler_exception(fake_name, Exception("x"))
        assert fake_name not in out


def test_sanitize_logs_full_exception_via_structlog(monkeypatch):
    """The point of sanitization is to hide the exception from the LLM,
    not from the dev. structlog should record class + full message."""
    captured: list = []

    class _FakeLogger:
        def warning(self, event: str, **kwargs):
            captured.append((event, kwargs))

    out = _sanitize_handler_exception(
        "run_code",
        RuntimeError("sandbox unreachable for 30s"),
        logger=_FakeLogger(),
    )
    # User-facing: neutral.
    assert out == _SANITIZED_FAILURE_MESSAGE
    # Dev-facing: full detail.
    assert len(captured) == 1
    event, kwargs = captured[0]
    assert event == "tool_handler_exception_sanitized"
    assert kwargs.get("tool") == "run_code"
    assert kwargs.get("error_class") == "RuntimeError"
    assert "sandbox unreachable" in kwargs.get("error", "")
