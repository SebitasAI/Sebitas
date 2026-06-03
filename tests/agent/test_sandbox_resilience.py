"""Sandbox lifecycle behavior pinned against two observed prod bugs.

Bug A (NameError, Simetrik trace 2026-06-02 19:11:25):
  `run_code(print(2+2))` returned `undefined` with `NameError: name 'links'
  is not defined`. Root cause: stale variable name in the final log.info
  call after the PR #123 rename `links -> artifact_summary`. The function
  ran, the artifacts were uploaded, then the log line crashed and the
  whole tool result came back as an error.

Bug B (sandbox reaped mid-run, Simetrik trace 2026-06-02 19:10:52):
  Agent ran for 7m45s, did several composio/slack calls between two
  run_code calls, the E2B VM hit the 300s default lifetime and was
  reaped server-side, the second run_code failed with
  `TimeoutException: ... "sandbox was not found", "code": 502`. The
  whole agent run died ($0.83, 1.6M tokens wasted).

These tests pin both fixes so a regression doesn't reintroduce either.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agent import sandbox as sandbox_mod


@pytest.fixture(autouse=True)
def _clear_sandbox_cache():
    sandbox_mod._sandboxes.clear()
    yield
    sandbox_mod._sandboxes.clear()


@pytest.fixture
def _run_context():
    """Inject a fake run + workspace into the contextvars run_code reads."""
    from app.agent.context import run_id_var, workspace_id_var
    tok1 = run_id_var.set("run-resilience-1")
    tok2 = workspace_id_var.set("ws-resilience-1")
    yield
    run_id_var.reset(tok1)
    workspace_id_var.reset(tok2)


def _fake_execution(stdout: str = "", err: object | None = None):
    return SimpleNamespace(
        error=err,
        logs=SimpleNamespace(stdout=[stdout] if stdout else [], stderr=[]),
    )


class TestSandboxReapedRecreate:
    def test_is_sandbox_gone_detects_502(self):
        exc = Exception('{"message":"The sandbox was not found","code":502}')
        assert sandbox_mod._is_sandbox_gone(exc) is True

    def test_is_sandbox_gone_ignores_other_errors(self):
        # Real network errors, OOM, etc. should NOT trigger the recreate
        # path -- recreating on every error masks legitimate bugs.
        assert sandbox_mod._is_sandbox_gone(Exception("connection refused")) is False
        assert sandbox_mod._is_sandbox_gone(Exception("HTTP 500")) is False
        assert sandbox_mod._is_sandbox_gone(Exception("rate limited")) is False

    def test_run_code_recreates_sandbox_after_reap(self, _run_context):
        """When the cached sandbox is reaped server-side, run_code must
        evict + recreate transparently, retry once, and surface a note
        to the LLM so it knows in-sandbox state was lost."""
        reaped = AsyncMock()
        reaped.run_code = AsyncMock(side_effect=Exception(
            '{"message":"The sandbox was not found","code":502}'
        ))
        fresh = AsyncMock()
        fresh.run_code = AsyncMock(return_value=_fake_execution(stdout="4"))

        creates: list[AsyncMock] = [reaped, fresh]

        async def _fake_create(**_kwargs):
            return creates.pop(0)

        with patch.object(sandbox_mod.AsyncSandbox, "create", side_effect=_fake_create), \
             patch.object(sandbox_mod, "_collect_artifacts", new=AsyncMock(return_value="")):
            result = asyncio.run(sandbox_mod.run_code("print(2+2)"))

        assert "sandbox anterior expiró" in result
        assert "4" in result
        # Two creates: the reaped one + the fresh one
        assert len(creates) == 0

    def test_run_code_does_not_recreate_on_unrelated_error(self, _run_context):
        """A non-reap error must propagate so we don't silently mask bugs."""
        sbx = AsyncMock()
        sbx.run_code = AsyncMock(side_effect=RuntimeError("e2b auth failure"))

        async def _fake_create(**_kwargs):
            return sbx

        with patch.object(sandbox_mod.AsyncSandbox, "create", side_effect=_fake_create):
            with pytest.raises(RuntimeError, match="e2b auth failure"):
                asyncio.run(sandbox_mod.run_code("print(1)"))


class TestRunCodeNoStaleVarBug:
    """Bug A regression: `run_code(print(2+2))` crashed with
    `NameError: name 'links' is not defined` at the log.info line.
    The function now logs `has_artifacts=bool(artifact_summary)` and
    never references an undefined name."""

    def test_run_code_with_no_artifacts_does_not_raise(self, _run_context):
        sbx = AsyncMock()
        sbx.run_code = AsyncMock(return_value=_fake_execution(stdout="4"))

        async def _fake_create(**_kwargs):
            return sbx

        with patch.object(sandbox_mod.AsyncSandbox, "create", side_effect=_fake_create), \
             patch.object(sandbox_mod, "_collect_artifacts", new=AsyncMock(return_value="")):
            result = asyncio.run(sandbox_mod.run_code("print(2+2)"))

        # No NameError, output flowed through.
        assert "4" in result

    def test_run_code_with_artifacts_does_not_raise(self, _run_context):
        sbx = AsyncMock()
        sbx.run_code = AsyncMock(return_value=_fake_execution(stdout=""))

        async def _fake_create(**_kwargs):
            return sbx

        with patch.object(sandbox_mod.AsyncSandbox, "create", side_effect=_fake_create), \
             patch.object(sandbox_mod, "_collect_artifacts",
                          new=AsyncMock(return_value="Subí 2 archivos a Slack")):
            result = asyncio.run(sandbox_mod.run_code("import pandas; pandas.DataFrame().to_csv('/home/user/outputs/x.csv')"))

        assert "Subí 2 archivos a Slack" in result
