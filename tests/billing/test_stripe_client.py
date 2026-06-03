"""Unit tests for the Stripe SDK wrapper. Mocks the underlying SDK
to keep tests fast + offline."""

from __future__ import annotations

import json

import pytest
import stripe


class TestIsConfigured:
    def test_both_keys_set(self, monkeypatch):
        from app.billing import stripe_client

        class _S:
            stripe_api_key = "sk_test_xxx"
            stripe_webhook_secret = "whsec_xxx"
            stripe_price_ids_json = None

        monkeypatch.setattr(stripe_client, "get_settings", lambda: _S())
        assert stripe_client.is_configured() is True

    def test_missing_api_key(self, monkeypatch):
        from app.billing import stripe_client

        class _S:
            stripe_api_key = None
            stripe_webhook_secret = "whsec_xxx"
            stripe_price_ids_json = None

        monkeypatch.setattr(stripe_client, "get_settings", lambda: _S())
        assert stripe_client.is_configured() is False

    def test_missing_webhook_secret(self, monkeypatch):
        from app.billing import stripe_client

        class _S:
            stripe_api_key = "sk_test_xxx"
            stripe_webhook_secret = None
            stripe_price_ids_json = None

        monkeypatch.setattr(stripe_client, "get_settings", lambda: _S())
        assert stripe_client.is_configured() is False


class TestPriceIds:
    def test_empty_when_unset(self, monkeypatch):
        from app.billing import stripe_client

        class _S:
            stripe_api_key = "sk_test_xxx"
            stripe_webhook_secret = "whsec_xxx"
            stripe_price_ids_json = None

        monkeypatch.setattr(stripe_client, "get_settings", lambda: _S())
        assert stripe_client.get_price_ids() == {}

    def test_parses_json(self, monkeypatch):
        from app.billing import stripe_client

        class _S:
            stripe_api_key = "sk_test_xxx"
            stripe_webhook_secret = "whsec_xxx"
            stripe_price_ids_json = json.dumps({
                "starter_monthly": "price_111",
                "starter_annual": "price_112",
            })

        monkeypatch.setattr(stripe_client, "get_settings", lambda: _S())
        ids = stripe_client.get_price_ids()
        assert ids["starter_monthly"] == "price_111"
        assert ids["starter_annual"] == "price_112"

    def test_malformed_returns_empty(self, monkeypatch):
        from app.billing import stripe_client

        class _S:
            stripe_api_key = "sk_test_xxx"
            stripe_webhook_secret = "whsec_xxx"
            stripe_price_ids_json = "not json {{{"

        monkeypatch.setattr(stripe_client, "get_settings", lambda: _S())
        # Should not raise; returns empty + logs.
        assert stripe_client.get_price_ids() == {}


class TestEnsureApiKey:
    def test_raises_when_unset(self, monkeypatch):
        from app.billing import stripe_client

        class _S:
            stripe_api_key = None
            stripe_webhook_secret = None
            stripe_price_ids_json = None

        monkeypatch.setattr(stripe_client, "get_settings", lambda: _S())
        monkeypatch.setattr(stripe_client, "_api_key_loaded", False)
        with pytest.raises(stripe_client.BillingNotConfiguredError):
            stripe_client._ensure_api_key()


class TestVerifyWebhookSignature:
    def test_missing_secret_raises(self, monkeypatch):
        from app.billing import stripe_client

        class _S:
            stripe_api_key = "sk_test_xxx"
            stripe_webhook_secret = None
            stripe_price_ids_json = None

        monkeypatch.setattr(stripe_client, "get_settings", lambda: _S())
        with pytest.raises(stripe_client.BillingNotConfiguredError):
            stripe_client.verify_webhook_signature(
                payload=b"{}", signature_header="t=0,v1=deadbeef"
            )

    def test_invalid_signature_raises(self, monkeypatch):
        from app.billing import stripe_client

        class _S:
            stripe_api_key = "sk_test_xxx"
            stripe_webhook_secret = "whsec_test_secret"
            stripe_price_ids_json = None

        monkeypatch.setattr(stripe_client, "get_settings", lambda: _S())
        with pytest.raises(stripe.SignatureVerificationError):
            stripe_client.verify_webhook_signature(
                payload=b'{"type":"test"}',
                signature_header="t=0,v1=deadbeef",
            )
