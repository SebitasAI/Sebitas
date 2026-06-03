"""Unit tests for the Stripe webhook dispatch + helpers. Pure-Python:
the SDK is mocked, no DB needed. Integration coverage (DB-backed
handler side effects) lives in test_repository.py / future
test_webhook_handlers_integration.py."""

from __future__ import annotations

import pytest

from app.billing import webhook_handlers


class TestDispatch:
    @pytest.mark.asyncio
    async def test_known_event_returns_true(self, monkeypatch):
        called = {"hit": False}

        async def fake_handler(event):  # noqa: ARG001
            called["hit"] = True

        monkeypatch.setitem(
            webhook_handlers.HANDLERS, "invoice.payment_failed", fake_handler
        )
        handled = await webhook_handlers.dispatch(
            {"type": "invoice.payment_failed", "data": {"object": {}}}
        )
        assert handled is True
        assert called["hit"] is True

    @pytest.mark.asyncio
    async def test_unknown_event_returns_false(self):
        handled = await webhook_handlers.dispatch(
            {"type": "this.does.not.exist", "data": {"object": {}}}
        )
        assert handled is False

    def test_all_six_events_registered(self):
        # Whitelist the events we promised to handle. If we ever drop
        # one accidentally during a refactor, this fails loudly.
        expected = {
            "checkout.session.completed",
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
            "invoice.payment_succeeded",
            "invoice.payment_failed",
        }
        assert set(webhook_handlers.HANDLERS.keys()) == expected


class TestMetadataParsers:
    def test_workspace_id_valid(self):
        meta = {"workspace_id": "00000000-0000-0000-0000-000000000001"}
        assert webhook_handlers._meta_workspace_id(meta) is not None

    def test_workspace_id_missing(self):
        assert webhook_handlers._meta_workspace_id({}) is None
        assert webhook_handlers._meta_workspace_id(None) is None

    def test_workspace_id_malformed(self):
        # Bad UUID returns None instead of raising. Prevents a typo
        # in Checkout metadata from crashing the webhook handler.
        assert webhook_handlers._meta_workspace_id({"workspace_id": "not-a-uuid"}) is None

    def test_plan_known(self):
        assert webhook_handlers._meta_plan({"plan": "starter"}) == "starter"
        assert webhook_handlers._meta_plan({"plan": "pro"}) == "pro"

    def test_plan_unknown_rejected(self):
        # We won't apply a plan we don't recognize. Returns None so
        # the handler treats it as a no-op.
        assert webhook_handlers._meta_plan({"plan": "premium_ultra"}) is None
        assert webhook_handlers._meta_plan({}) is None

    def test_cycle_valid(self):
        assert webhook_handlers._meta_cycle({"cycle": "monthly"}) == "monthly"
        assert webhook_handlers._meta_cycle({"cycle": "annual"}) == "annual"

    def test_cycle_unknown_rejected(self):
        assert webhook_handlers._meta_cycle({"cycle": "weekly"}) is None
        assert webhook_handlers._meta_cycle({}) is None


class TestTimestampConversion:
    def test_int_ts_to_dt(self):
        dt = webhook_handlers._ts_to_dt(1717200000)
        assert dt is not None
        assert dt.year == 2024
        assert dt.tzinfo is not None

    def test_none_returns_none(self):
        assert webhook_handlers._ts_to_dt(None) is None


class TestMonthlyPriceLookup:
    def test_known_plans(self):
        assert webhook_handlers._monthly_price_for_plan("starter") == 100.0
        assert webhook_handlers._monthly_price_for_plan("pro") == 400.0
        assert webhook_handlers._monthly_price_for_plan("scale") == 1_500.0
        assert webhook_handlers._monthly_price_for_plan("business") == 5_000.0

    def test_unknown_plan_returns_zero(self):
        assert webhook_handlers._monthly_price_for_plan("bogus") == 0.0
