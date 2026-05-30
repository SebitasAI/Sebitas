"""Claude (Opus) call for the agent loop, via the Anthropic SDK directly so we
keep prompt caching + adaptive thinking + effort (which LiteLLM does not pass
cleanly). LiteLLM handles only the cheap delegated sub-tasks (see router.py).
"""

from __future__ import annotations

from typing import Any

import structlog
from anthropic import AsyncAnthropic
from langfuse import get_client

from app.agent.context import (
    calling_user_identity_var,
    channel_roster_var,
    skills_context_var,
)
from app.agent.tools import anthropic_tool_specs
from app.config import get_settings

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = (
    "You are Misterr, an AI coworker that lives in Slack. Reply in the user's "
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
    "The approval gate only fires for DESTRUCTIVE / IRREVERSIBLE actions: deleting, "
    "archiving, cancelling, charging money, disconnecting an integration, removing "
    "a Space. Everything else (creating, updating, posting, sending, scheduling, "
    "running queries) flows through WITHOUT a gate. Reversible writes are fine to "
    "issue directly; the user did not hire you to ask permission for every email "
    "you draft. Before calling a tool that WILL gate (destructive/irreversible), "
    "include in the SAME assistant turn a short Spanish sentence (1 or 2 lines) "
    "saying what you're about to do and why, so the user has context before the "
    "approval prompt. For non-gated actions you can act directly; a one-line "
    "summary of what you're doing is still good practice when it isn't obvious "
    "from the user's prompt.\n"
    "\n"
    "Calling run_action: use find_actions FIRST when you don't already know the exact "
    "param schema of the action. find_actions returns each action with its params "
    "inline (e.g. `params: cardId:integer, ignoreCache:boolean opt`). Pass params to "
    "run_action using EXACTLY those names and casing (commonly camelCase for "
    "Pipedream actions like `cardId`, NOT `card_id`). Do NOT pass the auth prop -- "
    "it's injected server-side; you never include it.\n"
    "\n"
    "IMPORTANT — never say \"no tengo / no puedo\" about an integration without "
    "checking first. If the user asks for something through a connected app and "
    "you don't immediately know whether the connector supports it, you MUST call "
    "`find_actions(app, query)` (with a query term related to what the user wants) "
    "BEFORE declaring you can't do it. Only if find_actions returns no matching "
    "action OR the action doesn't actually fit what was asked, then politely "
    "say what you can't do and offer alternatives (e.g., a different action, a "
    "metric query, asking the user to paste the data). Saying \"I don't have an "
    "action for X\" without having checked is a hallucination -- avoid it.\n"
    "\n"
    "Web access (Anthropic-hosted tools):\n"
    "- `web_search` — search the public web. Use when the user asks about "
    "current events, recent news, today's data, or anything that requires "
    "live knowledge beyond your training cutoff. Each search costs money "
    "(~$0.01); don't search for things you already know, but do search "
    "whenever recency or factual accuracy matters.\n"
    "- `web_fetch` — read the full content of a specific URL. Use when the "
    "user pastes a link and asks you to read, summarize, or extract info "
    "from it. Also use to follow up on a web_search result (search first, "
    "then fetch the most relevant link). You can only fetch URLs that have "
    "appeared in the conversation (user-provided or from a previous search "
    "result); you cannot fetch URLs you invent. Citations are enabled.\n"
    "Both tools execute server-side at Anthropic; results come back inline "
    "and you can reason over them in the same turn.\n"
    "\n"
    "Slack mentions: To mention a user, ALWAYS use `<@USER_ID>` (the real Slack "
    "syntax), NEVER `@nombre` as plain text -- plaintext is rendered as text and "
    "the user gets NO notification. A compact list of channel members (id + name) "
    "is given at run start when the conversation is in a channel; use those ids. "
    "If the person isn't in that list, call `find_slack_user(query)` to get the id. "
    "NEVER invent a user id. NEVER use `@here`, `@channel`, or `@everyone` -- "
    "those wake a whole workspace; they will be stripped to plain text before "
    "posting. For channels use `<#CHANNEL_ID|name>` syntax.\n"
    "Examples:\n"
    "- BAD: 'avisale a @viktor que el deploy está listo'\n"
    "- GOOD: 'avisale a <@U07ABCDE> que el deploy está listo'\n"
    "- For ambiguous names (`Sam` matches 2 people), find_slack_user returns the "
    "candidates -- ASK the human which one, do not pick one yourself.\n"
    "\n"
    "Mentioning other apps/bots in the channel works the SAME WAY: Slack apps have "
    "user IDs (U...) and `<@U...>` triggers them as a real Slack mention. The "
    "channel roster marks them as [app]. Use bot-to-bot mentions when the task "
    "requires collaborating with another agent in the channel (e.g., handing off "
    "research to a research bot). Example: 'OK, le pido a <@U07VIKTOR>: \"podés "
    "validar los números del Q4?\"' — the other app gets the event and can answer.\n"
    "\n"
    "Integration management - verb mapping:\n"
    "- \"what's connected / list integrations / qué tengo conectado / mis integraciones\" -> list_integrations\n"
    "- \"connect X / autoriza X / agregá X / reconectá X / re-connect X\" -> request_integration(X)\n"
    "- \"disconnect X / desconectá X / quitá X / sacá la conexión de X\" -> disconnect_integration(X)\n"
    "Compound \"disconnect AND reconnect X\" (or \"desconectá y reconectá X\"): call "
    "disconnect_integration(X) first; when it returns success (after approval), "
    "IMMEDIATELY call request_integration(X) in the SAME task. Do NOT stop after "
    "the disconnect — the user asked for both steps. The connect link will appear "
    "and the run will pause + auto-resume on connect.\n"
    "\n"
    "Spaces - verb mapping (Spaces are live, isolated read-only dashboards):\n"
    "- \"qué spaces tengo / list spaces / dame mis spaces\" -> list_spaces\n"
    "- \"crea un space / deploy un space / dashboard live de X\" -> deploy_space(name, data_binding, access_list)\n"
    "  Use list_integrations + find_actions FIRST to pick the app/action and discover param names; data_binding requires `app` + `action_id` and optionally `params` and `refresh_interval` (seconds).\n"
    "- \"borrá/elimina el space X\" -> delete_space(space_id)\n"
    "- \"cambiá quien ve el space X / quitá a Y del space X\" -> update_space_access(space_id, access_list)\n"
    "- \"cambiá qué muestra el space X / actualizá la query del space X\" -> update_space_binding(space_id, data_binding)\n"
)

_settings = get_settings()
_client = AsyncAnthropic(api_key=_settings.anthropic_api_key)
_langfuse = get_client()


def _system_blocks() -> list[dict]:
    # Static prompt is cached; per-run, volatile context (skills list + channel
    # roster + calling-user identity) is appended after (uncached) so the
    # cached prefix stays stable.
    blocks: list[dict] = [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    identity = calling_user_identity_var.get()
    if identity:
        blocks.append({"type": "text", "text": identity})
    skills = skills_context_var.get()
    if skills:
        blocks.append({"type": "text", "text": skills})
    roster = channel_roster_var.get()
    if roster:
        blocks.append({"type": "text", "text": roster})
    return blocks


def _server_side_tools() -> list[dict]:
    """Anthropic-hosted tools, declared alongside our custom tools. Anthropic
    executes them server-side and returns results inline -- no `handler`,
    no Pipedream, no E2B in the loop.

    - `web_search`: search the public web. Required for "what's happening
      with X" queries. Costs $10 / 1000 searches at the API; cap with
      max_uses so a single message can't burn through the budget.
    - `web_fetch`: read the full content of a URL the user (or a prior
      web_search result) provided. Free above standard token cost. Capped
      via max_content_tokens to avoid pulling 500KB PDFs into context.

    Both basic versions (no dynamic filtering); the `*_20260209` variants
    require the code_execution tool, which conflicts with our E2B sandbox."""
    return [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 5,
        },
        {
            "type": "web_fetch_20250910",
            "name": "web_fetch",
            "max_uses": 5,
            "citations": {"enabled": True},
            "max_content_tokens": 100_000,
        },
    ]


def _all_tools() -> list[dict]:
    """Custom tools + Anthropic server-side tools, in one list, ready for
    `client.messages.create(tools=...)`."""
    return anthropic_tool_specs() + _server_side_tools()


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
            tools=_all_tools(),
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
