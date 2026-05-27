"""Single Claude call, wrapped in Langfuse tracing.

No agent loop, tools, or sandbox: text in, text out.
"""

from __future__ import annotations

import structlog
from anthropic import AsyncAnthropic
from langfuse import get_client

from app.config import get_settings

log = structlog.get_logger(__name__)

# Generic, use-case-agnostic system prompt. Marked with cache_control from the
# first call. NOTE: prompt caching only takes effect once the cached prefix
# reaches Opus 4.7's ~4096-token minimum; until this prompt grows (e.g. with
# skills later), the cache_control marker is a harmless no-op
# (usage.cache_creation_input_tokens stays 0).
SYSTEM_PROMPT = (
    "You are Sebitas, an AI coworker that lives in Slack. "
    "Be helpful, direct, and concise. Reply in the same language the user writes in. "
    "You have no specialized tools or domain knowledge yet; answer from general reasoning."
)

_settings = get_settings()
_client = AsyncAnthropic(api_key=_settings.anthropic_api_key)
_langfuse = get_client()


async def generate_reply(history: list[dict[str, str]]) -> str:
    """Send the conversation history to Claude and return the reply text.

    `history` is an ordered list of {"role": "user"|"assistant", "content": str}
    starting with a user turn, giving Claude memory within the conversation.
    """
    settings = get_settings()
    messages = history

    with _langfuse.start_as_current_observation(
        as_type="generation",
        name="sebitas-reply",
        model=settings.claude_model,
        input=messages,
    ) as generation:
        response = await _client.messages.create(
            model=settings.claude_model,
            max_tokens=settings.claude_max_tokens,
            thinking={"type": "adaptive"},  # Opus 4.7: off by default, enable explicitly
            output_config={"effort": settings.claude_effort},
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
        )
        text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        generation.update(
            output=text,
            usage_details={
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens,
                "cache_read": response.usage.cache_read_input_tokens or 0,
                "cache_write": response.usage.cache_creation_input_tokens or 0,
            },
        )

    log.info(
        "claude_reply",
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )
    return text or "(sin respuesta)"


def flush_langfuse() -> None:
    """Flush pending traces on shutdown."""
    _langfuse.flush()
