"""Claude (Opus) call for the agent loop, via the Anthropic SDK directly so we
keep prompt caching + adaptive thinking + effort (which LiteLLM does not pass
cleanly). LiteLLM handles only the cheap delegated sub-tasks (see router.py).
"""

from __future__ import annotations

from typing import Any

import structlog
from anthropic import AsyncAnthropic
from langfuse import get_client

from app.agent.context import skills_context_var
from app.agent.tools import anthropic_tool_specs
from app.config import get_settings

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = (
    "You are Sebitas, an AI coworker that lives in Slack. Reply in the user's "
    "language, concise and conversational, like a normal Slack chat message, not a "
    "formatted document. Use bold sparingly.\n"
    "Format with Slack mrkdwn, NOT standard Markdown:\n"
    "- Bold is *text* with SINGLE asterisks. NEVER use **text** or Markdown headings (#, ##).\n"
    "- Italics is _text_. Inline code or values use `backticks`.\n"
    "- For lists, start each line with '• '. Do not use '-' or '*' bullets.\n"
    "- Links are <https://url|label>.\n"
    "You can call tools to get information or act. Use them when they help, and you "
    "may request several independent tools at once. Some tools are risky and require "
    "human approval before running. When you have enough to answer, reply with a "
    "final message and no further tool calls.\n"
    "\n"
    "Integration management - verb mapping:\n"
    "- \"what's connected / list integrations / qué tengo conectado / mis integraciones\" -> list_integrations\n"
    "- \"connect X / autoriza X / agregá X / reconectá X / re-connect X\" -> request_integration(X)\n"
    "- \"disconnect X / desconectá X / quitá X / sacá la conexión de X\" -> disconnect_integration(X)\n"
    "Compound \"disconnect AND reconnect X\" (or \"desconectá y reconectá X\"): call "
    "disconnect_integration(X) first; when it returns success (after approval), "
    "IMMEDIATELY call request_integration(X) in the SAME task. Do NOT stop after "
    "the disconnect — the user asked for both steps. The connect link will appear "
    "and the run will pause + auto-resume on connect."
)

_settings = get_settings()
_client = AsyncAnthropic(api_key=_settings.anthropic_api_key)
_langfuse = get_client()


def _system_blocks() -> list[dict]:
    # Static prompt is cached; the per-run installed-skills list is appended after
    # it (uncached) so the cached prefix stays stable.
    blocks: list[dict] = [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    skills = skills_context_var.get()
    if skills:
        blocks.append({"type": "text", "text": skills})
    return blocks


async def call_claude(messages: list[dict]) -> Any:
    """One Opus turn with tools. Returns the raw Anthropic response so the caller
    can read text + tool_use blocks (and preserve thinking blocks across turns)."""
    settings = get_settings()
    with _langfuse.start_as_current_observation(
        as_type="generation",
        name="agent:claude",
        model=settings.claude_model,
        input=messages,
    ) as gen:
        response = await _client.messages.create(
            model=settings.claude_model,
            max_tokens=settings.claude_max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": settings.claude_effort},
            system=_system_blocks(),
            tools=anthropic_tool_specs(),
            messages=messages,
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        gen.update(
            output=text or "[tool_use]",
            usage_details={
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens,
                "cache_read": response.usage.cache_read_input_tokens or 0,
                "cache_write": response.usage.cache_creation_input_tokens or 0,
            },
        )
    log.info(
        "agent_claude_turn",
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        stop_reason=response.stop_reason,
    )
    return response


def flush_langfuse() -> None:
    _langfuse.flush()
