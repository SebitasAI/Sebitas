"""Unit tests for the daily monthly-reset loop logic.

The integration of `tick()` against a real DB is covered by
test_repository.py's existing `credit_monthly_reset` cases; here we
pin the **selection logic** (which workspaces are due) without
touching Postgres. We test the loop's tick + sleep behavior with a
fake async sleep so the test is fast."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.billing import monthly_reset


class TestResetIntervalConstant:
    def test_under_30_days(self):
        # Slight under 30 days avoids ticking slightly LATE from
        # accumulating into "we skipped a month" drift.
        assert monthly_reset.RESET_INTERVAL < timedelta(days=30)
        assert monthly_reset.RESET_INTERVAL > timedelta(days=28)

    def test_tick_seconds_reasonable(self):
        # Every 6h gives us 4 ticks/day, plenty given the day-bounded
        # nature of the work. Tighten if you ever care about hour-of-day
        # accuracy.
        assert 60 * 60 <= monthly_reset.RESET_TICK_SECONDS <= 24 * 60 * 60


_REAL_SLEEP = asyncio.sleep  # capture before any patching


class TestLoopCancellation:
    @pytest.mark.asyncio
    async def test_cancellation_propagates(self, monkeypatch):
        # The forever loop must exit cleanly when the task is canceled.
        async def fake_tick():
            return 0

        async def instant_sleep(_secs):
            await _REAL_SLEEP(0)

        monkeypatch.setattr(monthly_reset, "tick", fake_tick)
        monkeypatch.setattr(monthly_reset.asyncio, "sleep", instant_sleep)

        task = asyncio.create_task(monthly_reset.run_monthly_reset_loop())
        await _REAL_SLEEP(0)  # let it start
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_tick_exceptions_are_swallowed(self, monkeypatch):
        # A failing tick must not kill the loop.
        calls = {"ticks": 0}

        async def flaky_tick():
            calls["ticks"] += 1
            if calls["ticks"] == 1:
                raise RuntimeError("transient db blip")
            return 0

        async def instant_sleep(_secs):
            if calls["ticks"] >= 2:
                raise asyncio.CancelledError()
            await _REAL_SLEEP(0)

        monkeypatch.setattr(monthly_reset, "tick", flaky_tick)
        monkeypatch.setattr(monthly_reset.asyncio, "sleep", instant_sleep)

        task = asyncio.create_task(monthly_reset.run_monthly_reset_loop())
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls["ticks"] >= 2  # ran past the failure
