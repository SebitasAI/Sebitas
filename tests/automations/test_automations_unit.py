"""Unit tests for automations that don't touch Postgres or external APIs.

Covers the pure-Python pieces of the source-driven design:

- `actions.SafeDict` + `render_template`: nested key interpolation
  (`{data.field}`), unknown keys stay literal, list indexing
  (`{items.0}`), non-string values stringify.
- `repository.generate_webhook_secret`: shape + uniqueness over many
  calls.
- `repository.CreateAutomationInput` validation: per-source column
  requirements raise the right AutomationValidationError variant.
- `triggers.verify_pipedream_signature`: accepts a hand-rolled HMAC
  with the documented header format, rejects expired timestamps,
  rejects mismatched signatures.
- `triggers.verify_composio_signature`: accepts a hand-rolled HMAC,
  rejects expired timestamps, rejects mismatched signatures.
- `router.MAX_FIRE_DEPTH` semantics via the contextvar.

Integration tests (DB + CRUD + permissions + webhook flow) require
TEST_DATABASE_URL and live in a future test_automations_integration.py.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from typing import Any

import pytest

from app.automations import repository as repo
from app.automations import triggers as trig
from app.automations.actions import SafeDict, render_template
from app.automations.router import (
    MAX_FIRE_DEPTH,
    _fire_depth_var,
    current_fire_depth,
)


# --------------------------------------------------------------------------- #
# Template engine
# --------------------------------------------------------------------------- #


class TestRenderTemplate:
    def test_flat_keys(self):
        out = render_template("error: {message}", {"message": "boom"})
        assert out == "error: boom"

    def test_nested_keys(self):
        payload = {"data": {"trace_id": "trc_123", "score": 0.4}}
        out = render_template(
            "Trace {data.trace_id} got score {data.score}", payload
        )
        assert out == "Trace trc_123 got score 0.4"

    def test_list_index(self):
        out = render_template("first={tags.0}", {"tags": ["urgent", "bug"]})
        assert out == "first=urgent"

    def test_unknown_keys_stay_literal(self):
        out = render_template("a={a} missing={missing}", {"a": "1"})
        assert out == "a=1 missing={missing}"

    def test_whole_payload(self):
        out = render_template("got: {payload}", {"k": "v"})
        # Whole-payload reference is JSON-encoded.
        assert '"k"' in out and '"v"' in out

    def test_non_string_values_stringify(self):
        out = render_template(
            "n={count} ok={flag}", {"count": 5, "flag": True}
        )
        assert out == "n=5 ok=True"

    def test_null_value(self):
        out = render_template("v={x}", {"x": None})
        assert out == "v="

    def test_safedict_missing_directly(self):
        sd = SafeDict({"present": "yes"})
        assert "{present} and {absent}".format_map(sd) == "yes and {absent}"


# --------------------------------------------------------------------------- #
# Webhook secret
# --------------------------------------------------------------------------- #


class TestWebhookSecret:
    def test_length(self):
        s = repo.generate_webhook_secret()
        # token_urlsafe(32) -> 43-char base64url string (no padding).
        assert 40 <= len(s) <= 64

    def test_uniqueness(self):
        # Cheap collision check across a small sample. 32 bytes is
        # 2^256 keyspace so this is overkill, but the goal is to catch
        # accidental constant returns / RNG misconfigurations.
        seen = {repo.generate_webhook_secret() for _ in range(100)}
        assert len(seen) == 100

    def test_charset(self):
        s = repo.generate_webhook_secret()
        # base64url: letters, digits, -, _
        assert all(c.isalnum() or c in "-_" for c in s)


# --------------------------------------------------------------------------- #
# CreateAutomationInput per-source validation (without DB)
# --------------------------------------------------------------------------- #


def _ci(**overrides: Any) -> repo.CreateAutomationInput:
    """Build a CreateAutomationInput with sensible defaults plus
    overrides. The defaults intentionally produce a VALID `direct`
    payload so tests only specify what they're flexing."""
    base = dict(
        workspace_id=uuid.uuid4(),
        created_by_user_id=uuid.uuid4(),
        name="my-automation",
        description=None,
        source="direct",
        prompt_template="hello {name}",
        destination_channel=None,
        trigger_metadata={},
    )
    base.update(overrides)
    return repo.CreateAutomationInput(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
class TestCreateValidation:
    """We can't call create_automation (DB) but we can exercise the
    pre-flight validations by patching get_session to raise before
    any SQL runs. Simpler path: assert the validators raise on the
    right inputs directly via the public function up to the DB call."""

    async def test_bad_slug(self, monkeypatch):
        async def _no_db(*_a, **_kw):
            raise AssertionError("should not reach DB")

        monkeypatch.setattr(
            "app.automations.repository.get_session", _no_db
        )
        with pytest.raises(repo.AutomationValidationError) as exc:
            await repo.create_automation(_ci(name="BadName"))
        assert "kebab-case" in str(exc.value)

    async def test_unknown_source(self, monkeypatch):
        monkeypatch.setattr(
            "app.automations.repository.get_session",
            lambda: (_ for _ in ()).throw(AssertionError("nope")),
        )
        with pytest.raises(repo.AutomationValidationError) as exc:
            await repo.create_automation(_ci(source="email"))
        assert "source" in str(exc.value).lower()

    async def test_empty_prompt(self, monkeypatch):
        monkeypatch.setattr(
            "app.automations.repository.get_session",
            lambda: (_ for _ in ()).throw(AssertionError("nope")),
        )
        with pytest.raises(repo.AutomationValidationError) as exc:
            await repo.create_automation(_ci(prompt_template="  "))
        assert "prompt_template" in str(exc.value)

    async def test_direct_rejects_external_id(self, monkeypatch):
        monkeypatch.setattr(
            "app.automations.repository.get_session",
            lambda: (_ for _ in ()).throw(AssertionError("nope")),
        )
        with pytest.raises(repo.AutomationValidationError):
            await repo.create_automation(
                _ci(source="direct", external_trigger_id="pd_abc")
            )

    async def test_pipedream_requires_id_and_key(self, monkeypatch):
        monkeypatch.setattr(
            "app.automations.repository.get_session",
            lambda: (_ for _ in ()).throw(AssertionError("nope")),
        )
        with pytest.raises(repo.AutomationValidationError):
            await repo.create_automation(_ci(source="pipedream"))
        with pytest.raises(repo.AutomationValidationError):
            await repo.create_automation(
                _ci(source="pipedream", external_trigger_id="pd_abc")
            )

    async def test_composio_rejects_plaintext_key(self, monkeypatch):
        monkeypatch.setattr(
            "app.automations.repository.get_session",
            lambda: (_ for _ in ()).throw(AssertionError("nope")),
        )
        with pytest.raises(repo.AutomationValidationError):
            await repo.create_automation(
                _ci(
                    source="composio",
                    external_trigger_id="cm_xyz",
                    external_trigger_key_plaintext="should-not-be-here",
                )
            )


# --------------------------------------------------------------------------- #
# Pipedream signature verification
# --------------------------------------------------------------------------- #


def _pd_sign(body: bytes, key: str, ts: int) -> str:
    payload = f"{ts}.".encode() + body
    digest = hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


class TestPipedreamSignature:
    def test_accepts_valid(self):
        body = b'{"event":"created"}'
        key = "k_test_123"
        ts = int(time.time())
        sig = _pd_sign(body, key, ts)
        assert trig.verify_pipedream_signature(
            raw_body=body, signature_header=sig, signing_key=key
        )

    def test_rejects_expired(self):
        body = b"{}"
        key = "k"
        ts = int(time.time()) - 10_000  # >5min ago
        sig = _pd_sign(body, key, ts)
        assert not trig.verify_pipedream_signature(
            raw_body=body, signature_header=sig, signing_key=key
        )

    def test_rejects_wrong_key(self):
        body = b"{}"
        ts = int(time.time())
        sig = _pd_sign(body, "real_key", ts)
        assert not trig.verify_pipedream_signature(
            raw_body=body, signature_header=sig, signing_key="wrong_key"
        )

    def test_rejects_tampered_body(self):
        ts = int(time.time())
        sig = _pd_sign(b"original", "k", ts)
        assert not trig.verify_pipedream_signature(
            raw_body=b"tampered", signature_header=sig, signing_key="k"
        )

    def test_rejects_bad_header_shape(self):
        ts = int(time.time())
        assert not trig.verify_pipedream_signature(
            raw_body=b"x", signature_header=f"t={ts}", signing_key="k"
        )
        assert not trig.verify_pipedream_signature(
            raw_body=b"x", signature_header="", signing_key="k"
        )


# --------------------------------------------------------------------------- #
# Composio signature verification
# --------------------------------------------------------------------------- #


def _composio_sign(body: bytes, secret: str, webhook_id: str, ts: int) -> str:
    payload = f"{webhook_id}.{ts}.".encode() + body
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


class TestComposioSignature:
    def test_accepts_valid(self, monkeypatch):
        # The verifier reads the secret from settings; stub it.
        secret = "composio_test_secret"
        monkeypatch.setenv("COMPOSIO_WEBHOOK_SECRET", secret)
        # Re-init the cached settings so the env override is visible.
        from app.config import get_settings as _get

        _get.cache_clear()  # type: ignore[attr-defined]

        body = b'{"x":1}'
        webhook_id = "msg_abc"
        ts = int(time.time())
        sig = _composio_sign(body, secret, webhook_id, ts)
        assert trig.verify_composio_signature(
            raw_body=body,
            webhook_id=webhook_id,
            webhook_timestamp=str(ts),
            signature_header=sig,
        )

    def test_rejects_expired(self, monkeypatch):
        secret = "s"
        monkeypatch.setenv("COMPOSIO_WEBHOOK_SECRET", secret)
        from app.config import get_settings as _get

        _get.cache_clear()  # type: ignore[attr-defined]
        ts = int(time.time()) - 9999
        sig = _composio_sign(b"x", secret, "w1", ts)
        assert not trig.verify_composio_signature(
            raw_body=b"x",
            webhook_id="w1",
            webhook_timestamp=str(ts),
            signature_header=sig,
        )

    def test_rejects_tampered_body(self, monkeypatch):
        secret = "s"
        monkeypatch.setenv("COMPOSIO_WEBHOOK_SECRET", secret)
        from app.config import get_settings as _get

        _get.cache_clear()  # type: ignore[attr-defined]
        ts = int(time.time())
        sig = _composio_sign(b"original", secret, "w1", ts)
        assert not trig.verify_composio_signature(
            raw_body=b"tampered",
            webhook_id="w1",
            webhook_timestamp=str(ts),
            signature_header=sig,
        )


# --------------------------------------------------------------------------- #
# Loop guard contextvar
# --------------------------------------------------------------------------- #


class TestFireDepth:
    def test_default_zero(self):
        assert current_fire_depth() == 0

    def test_set_and_reset(self):
        token = _fire_depth_var.set(2)
        try:
            assert current_fire_depth() == 2
        finally:
            _fire_depth_var.reset(token)
        assert current_fire_depth() == 0

    def test_max_depth_constant(self):
        # The loop guard constant is part of the API; pin it to 2 so
        # we notice if someone changes it without thinking about it.
        assert MAX_FIRE_DEPTH == 2
