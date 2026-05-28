"""Post-processor: convert plaintext mentions (`@viktor`, `#general`) into
Slack's real mention syntax (`<@U12345>`, `<#C12345|general>`) BEFORE we post
to Slack. Otherwise Slack just renders them as text and the target user gets
no notification.

Safety net: runs on every outbound message from the agent (preamble, final
reply, etc.). Strict policy on mass mentions (`@here`, `@channel`,
`@everyone`): blocked by default, rewritten to plaintext, logged.

Edge cases handled:
- Code blocks (```...``` and `inline`) are skipped wholesale.
- Email addresses (`x@y.com`) skipped via `\B` boundary.
- Already-formatted mentions (`<@U...>`) are left intact.
- Ambiguous matches → leave as plaintext (safer than mentioning the wrong
  person).

Edge cases NOT handled (honest flags):
- Multi-word names ("@Sam Santa"): only the first token is matched; the agent
  should use find_slack_user for multi-word names instead.
- Mentions inside blockquotes are processed normally (low risk).
"""

from __future__ import annotations

import re
import uuid

import structlog
from langfuse import get_client

from app.slack import roster

log = structlog.get_logger(__name__)
_langfuse = get_client()


_MASS_MENTION_TOKENS = ("here", "channel", "everyone")
# `<!here>` / `<!channel>` / `<!everyone>` are the bracketed forms Slack
# accepts. If the model emits them directly we strip too.
_MASS_BRACKETED_RE = re.compile(r"<!(here|channel|everyone)(?:\|[^>]*)?>")
_MASS_PLAIN_RE = re.compile(r"\B@(here|channel|everyone)\b", re.IGNORECASE)

# `\B@<name>` -- `\B` prevents matching inside emails (word|@|word boundary).
_USER_MENTION_RE = re.compile(r"\B@([A-Za-z][A-Za-z0-9._-]*)\b")
# `#<name>` -- channels. Same boundary trick. Slack channel names are
# lowercase + numbers + dashes + underscores.
_CHANNEL_MENTION_RE = re.compile(r"\B#([a-z0-9][a-z0-9._-]*)\b")

# Match fenced (```...```) AND inline (`...`) code regions. Multiline; non-greedy.
_CODE_REGION_RE = re.compile(r"```.*?```|`[^`\n]+`", re.DOTALL)


# --------------------------------------------------------------------------- #
# Main entry
# --------------------------------------------------------------------------- #


async def render_for_slack(
    text: str,
    *,
    workspace_id: uuid.UUID | None,
    channel_id: str | None = None,
) -> str:
    """Rewrite plaintext mentions to Slack syntax. workspace_id=None disables
    user/channel resolution (e.g. system-internal posts); mass mentions are
    still blocked."""
    if not text:
        return text
    # Mass mentions are always blocked, even without workspace context.
    text = _block_mass_mentions(text)

    if workspace_id is None:
        return text

    # Process only the non-code segments to avoid touching code samples.
    out: list[str] = []
    last_end = 0
    for m in _CODE_REGION_RE.finditer(text):
        # Process whatever came before this code region.
        prefix = text[last_end:m.start()]
        out.append(await _resolve_segment(prefix, workspace_id))
        out.append(m.group(0))  # preserve code region verbatim
        last_end = m.end()
    out.append(await _resolve_segment(text[last_end:], workspace_id))
    return "".join(out)


async def _resolve_segment(segment: str, workspace_id: uuid.UUID) -> str:
    if not segment:
        return segment
    segment = await _resolve_user_mentions(segment, workspace_id)
    segment = await _resolve_channel_mentions(segment, workspace_id)
    return segment


# --------------------------------------------------------------------------- #
# User mentions
# --------------------------------------------------------------------------- #


async def _resolve_user_mentions(text: str, workspace_id: uuid.UUID) -> str:
    """For each `@token`, try to resolve against the workspace roster. Unique
    match -> rewrite; ambiguous/miss -> leave alone + log."""
    # Collect all candidate tokens first to batch lookups (one DB hit per
    # unique token in this segment).
    tokens = sorted({m.group(1) for m in _USER_MENTION_RE.finditer(text)})
    if not tokens:
        return text

    resolved: dict[str, str] = {}  # token -> user_id (only unique matches)
    for tok in tokens:
        candidates = await roster.find_user(workspace_id, tok)
        # Filter bots (don't auto-mention them).
        candidates = [c for c in candidates if not c.get("is_bot")]
        if len(candidates) == 1:
            resolved[tok] = candidates[0]["slack_user_id"]
        elif len(candidates) == 0:
            log.info("mention_unresolved", token=tok, workspace_id=str(workspace_id))
        else:
            ids = [c["slack_user_id"] for c in candidates]
            log.info("mention_ambiguous", token=tok, candidates=ids, workspace_id=str(workspace_id))

    if not resolved:
        return text

    # Single pass replace, using a sub function so groups are correctly handled.
    def _sub(m: re.Match) -> str:
        tok = m.group(1)
        if tok in resolved:
            return f"<@{resolved[tok]}>"
        return m.group(0)

    return _USER_MENTION_RE.sub(_sub, text)


# --------------------------------------------------------------------------- #
# Channel mentions
# --------------------------------------------------------------------------- #


async def _resolve_channel_mentions(text: str, workspace_id: uuid.UUID) -> str:
    """For each `#name`, try to resolve against the slack_channel roster.
    Unique match -> rewrite to `<#C123|name>`; miss -> leave alone."""
    tokens = sorted({m.group(1) for m in _CHANNEL_MENTION_RE.finditer(text)})
    if not tokens:
        return text

    resolved: dict[str, str] = {}
    for tok in tokens:
        cid = await roster.find_channel(workspace_id, tok)
        if cid:
            resolved[tok] = cid
        else:
            log.info("channel_mention_unresolved", token=tok, workspace_id=str(workspace_id))

    if not resolved:
        return text

    def _sub(m: re.Match) -> str:
        tok = m.group(1)
        if tok in resolved:
            return f"<#{resolved[tok]}|{tok}>"
        return m.group(0)

    return _CHANNEL_MENTION_RE.sub(_sub, text)


# --------------------------------------------------------------------------- #
# Mass mentions: BLOCKED
# --------------------------------------------------------------------------- #


def _block_mass_mentions(text: str) -> str:
    """Rewrite @here / @channel / @everyone (and their bracketed forms) to
    plain text. The agent must not be able to wake a workspace by accident."""
    blocked_count = 0

    def _strip_bracketed(m: re.Match) -> str:
        nonlocal blocked_count
        blocked_count += 1
        return m.group(1)  # "here" / "channel" / "everyone"

    def _strip_plain(m: re.Match) -> str:
        nonlocal blocked_count
        blocked_count += 1
        return m.group(1).lower()

    text = _MASS_BRACKETED_RE.sub(_strip_bracketed, text)
    text = _MASS_PLAIN_RE.sub(_strip_plain, text)

    if blocked_count:
        log.info("mass_mention_blocked", count=blocked_count)
        try:
            # Surface to Langfuse as an event on the current observation, if any.
            with _langfuse.start_as_current_observation(
                as_type="event", name="mass_mention_blocked", input={"count": blocked_count}
            ):
                pass
        except Exception:  # noqa: BLE001
            pass

    return text
