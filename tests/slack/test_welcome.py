"""Tests for the install-time welcome DM.

What this pins:
  - The message body covers every basic capability the user is
    expected to learn on day one (mention, DM, channels, integrations,
    scheduled tasks, memory, actions).
  - Tone: neutral LatAm Spanish (tuteo), no voseo, no Argentinianisms,
    no internal subsystem names, no provider brand names.
  - The send function is idempotent across calls (the second one
    no-ops via the conditional UPDATE).
  - Slack call failures don't crash the install path; they're logged
    and the function returns False.
"""

from __future__ import annotations

import re
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.slack.welcome import (
    WELCOME_BODY,
    WELCOME_FALLBACK_TEXT,
    _build_blocks,
    maybe_send_welcome_dm,
)


# --------------------------------------------------------------------------- #
# Message-content contract
# --------------------------------------------------------------------------- #


def test_welcome_covers_every_basic_capability():
    """Sam's brief: greet + explain mention, DM, channels, integrations,
    skills/memory, scheduled tasks. Each topic should be discoverable
    by a substring scan."""
    body = WELCOME_BODY.lower()
    expected_topics = [
        "menciona",          # @-mention
        "dm",                # direct messages
        "canal",             # channels
        "conecta",           # integrations
        "programa",          # scheduled tasks
        "recuerda",          # memory / skills
        "salesforce",        # integration example
        "gmail",             # integration example
    ]
    for topic in expected_topics:
        assert topic in body, f"welcome message missing topic: {topic!r}"


def test_welcome_uses_tuteo_not_voseo():
    """Hard ban on Argentine voseo forms. Spot-check the most common
    ones; the prompt-level guardrail (PR #120) covers the rest."""
    body = WELCOME_BODY
    forbidden = [
        # Voseo imperatives.
        "podés", "intentá", "traeme", "mandame", "decime", "tenés",
        "querés", "usá", "reintentá", "enseñame", "dale",
        # Argentinianisms.
        "bárbaro", "che", "pibe", "laburar",
    ]
    body_lower = body.lower()
    for word in forbidden:
        assert word not in body_lower, (
            f"welcome message contains banned voseo/argentinianism: {word!r}"
        )
    # And positive check: at least some tuteo forms must appear.
    assert "puedes" in body_lower
    assert "pídeme" in body_lower or "pregúntame" in body_lower


def test_welcome_does_not_leak_internal_terms():
    """No provider brand names, no internal subsystem identifiers."""
    body_lower = WELCOME_BODY.lower()
    forbidden = [
        "pipedream", "composio", "anthropic", "claude",
        "sandbox", "e2b", "langfuse", "litellm", "langgraph",
        "neon", "cloudflare", "r2 ",
        "run_action", "load_skill", "find_in_action",
        "gateway", "runner", "skill body", "auto-improve",
    ]
    for term in forbidden:
        assert term not in body_lower, (
            f"welcome message leaks internal term: {term!r}"
        )


def test_fallback_text_is_short_and_friendly():
    """The fallback fires in notification UIs; keep it punchy."""
    assert len(WELCOME_FALLBACK_TEXT) < 100
    assert "Misterr" in WELCOME_FALLBACK_TEXT
    assert "Hola" in WELCOME_FALLBACK_TEXT


def test_build_blocks_returns_valid_slack_section():
    """Slack expects a list of blocks, each with `type` + `text`."""
    blocks = _build_blocks()
    assert isinstance(blocks, list)
    assert len(blocks) >= 1
    block = blocks[0]
    assert block["type"] == "section"
    assert block["text"]["type"] == "mrkdwn"
    assert block["text"]["text"] == WELCOME_BODY


# --------------------------------------------------------------------------- #
# Send function: idempotency + error handling
# --------------------------------------------------------------------------- #


def _fake_update_result(rowcount: int, bot_token_enc: str | None = "ENC"):
    """Build the result object that the conditional UPDATE yields."""
    result = MagicMock()
    if rowcount > 0:
        result.first = MagicMock(return_value=(bot_token_enc,))
    else:
        result.first = MagicMock(return_value=None)
    return result


def _session_ctx_for(update_result):
    """Return an async-context-manager that yields a session whose
    `execute` returns `update_result` and whose `commit` is a no-op."""
    session = MagicMock()
    session.execute = AsyncMock(return_value=update_result)
    session.commit = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.mark.asyncio
async def test_send_no_op_when_installer_empty():
    """Empty installer id -> no DB hit, no Slack call, returns False."""
    with patch("app.slack.welcome.get_session") as gs:
        out = await maybe_send_welcome_dm(
            workspace_id=uuid.uuid4(),
            installer_slack_user_id="",
        )
    assert out is False
    gs.assert_not_called()


@pytest.mark.asyncio
async def test_send_no_op_when_already_sent():
    """Conditional UPDATE returns 0 rows -> already sent, skip Slack."""
    with patch(
        "app.slack.welcome.get_session",
        return_value=_session_ctx_for(_fake_update_result(rowcount=0)),
    ), patch("slack_sdk.web.async_client.AsyncWebClient") as Client:
        out = await maybe_send_welcome_dm(
            workspace_id=uuid.uuid4(),
            installer_slack_user_id="U_INSTALLER",
        )
    assert out is False
    Client.assert_not_called()


@pytest.mark.asyncio
async def test_send_no_op_when_workspace_has_no_token():
    """Slot reserved but bot_token is NULL -> skip Slack."""
    with patch(
        "app.slack.welcome.get_session",
        return_value=_session_ctx_for(
            _fake_update_result(rowcount=1, bot_token_enc=None)
        ),
    ), patch("slack_sdk.web.async_client.AsyncWebClient") as Client:
        out = await maybe_send_welcome_dm(
            workspace_id=uuid.uuid4(),
            installer_slack_user_id="U_INSTALLER",
        )
    assert out is False
    Client.assert_not_called()


@pytest.mark.asyncio
async def test_send_happy_path_calls_chat_postMessage_on_installer():
    """First call wins the slot, decrypts the token, sends the DM to
    the installer's user id, returns True."""
    post = AsyncMock()
    client = MagicMock()
    client.chat_postMessage = post

    with patch(
        "app.slack.welcome.get_session",
        return_value=_session_ctx_for(
            _fake_update_result(rowcount=1, bot_token_enc="ENC"),
        ),
    ), patch(
        "app.slack.welcome.decrypt_token", return_value="xoxb-real",
    ), patch(
        "slack_sdk.web.async_client.AsyncWebClient", return_value=client,
    ):
        out = await maybe_send_welcome_dm(
            workspace_id=uuid.uuid4(),
            installer_slack_user_id="U_INSTALLER",
        )

    assert out is True
    post.assert_awaited_once()
    kwargs = post.await_args.kwargs
    assert kwargs["channel"] == "U_INSTALLER"
    assert kwargs["text"] == WELCOME_FALLBACK_TEXT
    assert kwargs["blocks"][0]["text"]["text"] == WELCOME_BODY


@pytest.mark.asyncio
async def test_send_returns_false_on_slack_error_without_retry_loop():
    """Slack rejected the post. We logged it, but we DO NOT roll back
    the welcome_dm_sent_at flag -- the next call won't try again. We
    explicitly prefer missed welcome over double-DM on retries."""
    post = AsyncMock(side_effect=Exception("rate_limited"))
    client = MagicMock()
    client.chat_postMessage = post

    with patch(
        "app.slack.welcome.get_session",
        return_value=_session_ctx_for(
            _fake_update_result(rowcount=1, bot_token_enc="ENC"),
        ),
    ), patch(
        "app.slack.welcome.decrypt_token", return_value="xoxb-real",
    ), patch(
        "slack_sdk.web.async_client.AsyncWebClient", return_value=client,
    ):
        out = await maybe_send_welcome_dm(
            workspace_id=uuid.uuid4(),
            installer_slack_user_id="U_INSTALLER",
        )

    assert out is False
    post.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_returns_false_on_decrypt_failure():
    """Token blob is unreadable; fall through to False without
    attempting a Slack call (we'd have to send a bogus token)."""
    from app.slack.crypto import TokenCryptoError

    with patch(
        "app.slack.welcome.get_session",
        return_value=_session_ctx_for(
            _fake_update_result(rowcount=1, bot_token_enc="ENC_BROKEN"),
        ),
    ), patch(
        "app.slack.welcome.decrypt_token",
        side_effect=TokenCryptoError("bad ciphertext"),
    ), patch("slack_sdk.web.async_client.AsyncWebClient") as Client:
        out = await maybe_send_welcome_dm(
            workspace_id=uuid.uuid4(),
            installer_slack_user_id="U_INSTALLER",
        )

    assert out is False
    Client.assert_not_called()
