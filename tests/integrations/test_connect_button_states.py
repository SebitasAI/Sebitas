"""Unit tests for the connect-button state transitions.

`_deactivate_connect_buttons` is the helper that replaces every Connect-X
button we posted in Slack with a passive status line, once a connect
attempt resolves. Two outcomes:

  - "connected": green check + "Sigo con tu pedido."
  - "failed":    red X + instruction to ask again.

Tests pin: correct text per outcome, idempotency on empty/missing
inputs, graceful handling of Slack errors, no-token short-circuit.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.integrations.connect import _deactivate_connect_buttons


@pytest.fixture
def _ws() -> uuid.UUID:
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def _buttons() -> list[dict]:
    return [
        {"channel": "C1", "ts": "1.1"},
        {"channel": "C1", "ts": "1.2"},
    ]


@pytest.mark.asyncio
async def test_no_buttons_no_op(_ws):
    """Empty buttons list: nothing to update, no Slack call."""
    with patch(
        "app.slack.tokens.get_bot_token_by_workspace", new=AsyncMock(),
    ) as get_token:
        await _deactivate_connect_buttons(_ws, "gong", [], outcome="connected")
    get_token.assert_not_called()


@pytest.mark.asyncio
async def test_no_token_logs_and_skips(_ws, _buttons):
    """No bot token for workspace -> log warning, skip Slack updates."""
    with patch(
        "app.slack.tokens.get_bot_token_by_workspace",
        new=AsyncMock(return_value=None),
    ), patch("slack_sdk.web.async_client.AsyncWebClient") as Client:
        await _deactivate_connect_buttons(
            _ws, "gong", _buttons, outcome="connected",
        )
    Client.assert_not_called()


@pytest.mark.asyncio
async def test_connected_outcome_uses_success_markdown(_ws, _buttons):
    update = AsyncMock()
    client = AsyncMock()
    client.chat_update = update

    with patch(
        "app.slack.tokens.get_bot_token_by_workspace",
        new=AsyncMock(return_value=("xoxb-abc", "T1")),
    ), patch(
        "slack_sdk.web.async_client.AsyncWebClient", return_value=client,
    ):
        await _deactivate_connect_buttons(
            _ws, "gong", _buttons, outcome="connected",
        )

    assert update.await_count == 2
    for call in update.await_args_list:
        kwargs = call.kwargs
        assert kwargs["text"] == "Conectado a gong."
        block_text = kwargs["blocks"][0]["text"]["text"]
        assert ":white_check_mark:" in block_text
        assert "Conectado a gong" in block_text
        assert "Sigo con tu pedido" in block_text


@pytest.mark.asyncio
async def test_failed_outcome_uses_failure_markdown_with_retry_instruction(
    _ws, _buttons,
):
    update = AsyncMock()
    client = AsyncMock()
    client.chat_update = update

    with patch(
        "app.slack.tokens.get_bot_token_by_workspace",
        new=AsyncMock(return_value=("xoxb-abc", "T1")),
    ), patch(
        "slack_sdk.web.async_client.AsyncWebClient", return_value=client,
    ):
        await _deactivate_connect_buttons(
            _ws, "salesforce", _buttons, outcome="failed",
        )

    assert update.await_count == 2
    for call in update.await_args_list:
        kwargs = call.kwargs
        assert "No se pudo conectar a salesforce" in kwargs["text"]
        block_text = kwargs["blocks"][0]["text"]["text"]
        assert ":x:" in block_text
        assert "No pude conectar a salesforce" in block_text
        # Must give the user an explicit retry instruction.
        assert "conectar salesforce" in block_text


@pytest.mark.asyncio
async def test_unknown_outcome_is_safe(_ws, _buttons):
    """Unknown outcome string -> log + skip, don't crash and don't
    push some random message to Slack."""
    update = AsyncMock()
    client = AsyncMock()
    client.chat_update = update

    with patch(
        "app.slack.tokens.get_bot_token_by_workspace",
        new=AsyncMock(return_value=("xoxb-abc", "T1")),
    ), patch(
        "slack_sdk.web.async_client.AsyncWebClient", return_value=client,
    ):
        await _deactivate_connect_buttons(
            _ws, "gong", _buttons, outcome="weird",
        )
    update.assert_not_called()


@pytest.mark.asyncio
async def test_one_slack_failure_does_not_break_the_others(_ws):
    """If chat_update fails for one ts, the rest still update."""
    buttons = [
        {"channel": "C1", "ts": "1.1"},
        {"channel": "C1", "ts": "1.2"},
        {"channel": "C1", "ts": "1.3"},
    ]
    update = AsyncMock(side_effect=[Exception("boom"), None, None])
    client = AsyncMock()
    client.chat_update = update

    with patch(
        "app.slack.tokens.get_bot_token_by_workspace",
        new=AsyncMock(return_value=("xoxb-abc", "T1")),
    ), patch(
        "slack_sdk.web.async_client.AsyncWebClient", return_value=client,
    ):
        await _deactivate_connect_buttons(
            _ws, "gong", buttons, outcome="connected",
        )
    assert update.await_count == 3  # all three attempts made


@pytest.mark.asyncio
async def test_buttons_without_channel_or_ts_are_skipped(_ws):
    """Malformed button entries don't crash the loop."""
    buttons = [
        {"channel": "C1"},               # no ts
        {"ts": "1.2"},                   # no channel
        {"channel": "C1", "ts": "1.3"},  # valid
    ]
    update = AsyncMock()
    client = AsyncMock()
    client.chat_update = update

    with patch(
        "app.slack.tokens.get_bot_token_by_workspace",
        new=AsyncMock(return_value=("xoxb-abc", "T1")),
    ), patch(
        "slack_sdk.web.async_client.AsyncWebClient", return_value=client,
    ):
        await _deactivate_connect_buttons(
            _ws, "gong", buttons, outcome="connected",
        )
    assert update.await_count == 1  # only the valid one
