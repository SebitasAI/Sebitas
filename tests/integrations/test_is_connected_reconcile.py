"""is_connected self-healing reconciliation tests.

Pins the contract: when the local DB lacks a `connected` row but the
provider upstream has the account, is_connected reconciles inline and
returns True. The rate limit + 'no prior attempt = skip' invariants
are also pinned so the optimistic path doesn't hammer the provider on
every chatty agent turn.

The IntegrationProvider, the connect.complete call, and the DB session
are all mocked to keep this fast + offline. Production behavior is
covered by the existing integration smoke."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations import gateway


@pytest.fixture(autouse=True)
def _clear_reconcile_cache():
    gateway._recent_reconciles.clear()
    yield
    gateway._recent_reconciles.clear()


class TestIsConnectedFastPath:
    @pytest.mark.asyncio
    async def test_connected_row_skips_reconcile(self, monkeypatch):
        ws = uuid.uuid4()

        async def _fake_connection(*args, **kwargs):  # noqa: ARG001
            return MagicMock(spec=[])  # any non-None object means "found"

        reconcile_spy = AsyncMock(return_value=False)
        monkeypatch.setattr(gateway, "_connection", _fake_connection)
        monkeypatch.setattr(gateway, "_try_reconcile", reconcile_spy)

        result = await gateway.is_connected(ws, "salesforce")
        assert result is True
        reconcile_spy.assert_not_called()


class TestIsConnectedReconcile:
    @pytest.mark.asyncio
    async def test_reconcile_succeeds_returns_true(self, monkeypatch):
        ws = uuid.uuid4()
        # First call -> None (no connected row). After "reconcile" flips
        # the row, second call returns the row.
        connection_calls = [None, MagicMock(spec=[])]

        async def _fake_connection(*args, **kwargs):  # noqa: ARG001
            return connection_calls.pop(0) if connection_calls else None

        monkeypatch.setattr(gateway, "_connection", _fake_connection)
        monkeypatch.setattr(
            gateway, "_try_reconcile", AsyncMock(return_value=True)
        )

        result = await gateway.is_connected(ws, "salesforce")
        assert result is True

    @pytest.mark.asyncio
    async def test_reconcile_fails_returns_false(self, monkeypatch):
        ws = uuid.uuid4()

        async def _fake_connection(*args, **kwargs):  # noqa: ARG001
            return None

        monkeypatch.setattr(gateway, "_connection", _fake_connection)
        monkeypatch.setattr(
            gateway, "_try_reconcile", AsyncMock(return_value=False)
        )

        result = await gateway.is_connected(ws, "salesforce")
        assert result is False


class TestReconcileRateLimit:
    @pytest.mark.asyncio
    async def test_recent_attempt_short_circuits(self, monkeypatch):
        ws = uuid.uuid4()
        # Pre-seed: this (ws, app) was reconciled "just now". Subsequent
        # call must not touch the provider at all.
        import time
        gateway._recent_reconciles[(str(ws), "salesforce")] = time.monotonic()

        provider_lookup = AsyncMock()
        monkeypatch.setattr(gateway, "provider_for_app", provider_lookup)

        result = await gateway._try_reconcile(ws, "salesforce")
        assert result is False
        provider_lookup.assert_not_called()


class TestReconcileSkipsWhenNoPriorAttempt:
    @pytest.mark.asyncio
    async def test_no_row_short_circuits(self, monkeypatch):
        """If the workspace never tried to connect this app at all,
        don't hit the provider. Otherwise every is_connected check for
        a random app would fan out an API call."""
        ws = uuid.uuid4()

        # get_session yields a context manager whose session.execute
        # returns a result whose .scalars().first() is None.
        class _Result:
            def scalars(self):
                return self
            def first(self):
                return None

        class _Session:
            async def execute(self, _stmt):
                return _Result()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_args):
                return None

        class _Ctx:
            def __call__(self):
                return self
            async def __aenter__(self):
                return _Session()
            async def __aexit__(self, *_args):
                return None

        monkeypatch.setattr(gateway, "get_session", _Ctx())
        provider_lookup = AsyncMock()
        monkeypatch.setattr(gateway, "provider_for_app", provider_lookup)

        result = await gateway._try_reconcile(ws, "some_random_app")
        assert result is False
        provider_lookup.assert_not_called()
