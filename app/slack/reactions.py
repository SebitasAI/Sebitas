"""Keyword-based reaction picker for Slack messages.

The agent runner adds a single emoji to each incoming user message as a
"I see you / I'm working on this" signal. Until now it was always the
hourglass; this module routes the chosen emoji based on the user's intent
so the bot reads with personality (👀 for analysis, 🙌 for praise, 🥹
for affection, etc.).

Important: this runs synchronously per message and MUST stay token-free.
No LLM calls. Pure regex matching against the user's text. If nothing
matches we fall back to the hourglass so the visibility-while-working
behavior is preserved.
"""

from __future__ import annotations

import re

# Slack emoji shortnames (without surrounding colons). reactions.add takes
# this exact string. If a customer's workspace has the same emoji but
# under a different shortname, add a fallback name in the rule's emoji
# field is currently not supported -- we'd need a list. v1: single name.
DEFAULT_REACTION: str = "hourglass_flowing_sand"


# Rule order matters: first match wins. The patterns are case-insensitive
# (compiled with re.IGNORECASE) and use word boundaries where it matters
# so we don't fire on substrings inside unrelated words ("creatividad"
# shouldn't match "crea").
_RULES: list[tuple[re.Pattern[str], str]] = [
    # Affection / tender messages — these win over "thanks" / others so a
    # heartfelt "te quiero un montón gracias" doesn't get a 🙏.
    (
        re.compile(
            r"\b(te\s+quiero|te\s+amo|love\s+you|i\s+love|cari[ñn]o|adoro|amor)\b",
            re.IGNORECASE,
        ),
        "smiling_face_with_3_hearts",
    ),
    # Praise / well done.
    (
        re.compile(
            r"\b(well\s?done|bien\s?hecho|excelente|brillante|genial|"
            r"perfecto|amazing|nice\s?job|crack|maestro)\b",
            re.IGNORECASE,
        ),
        "raised_hands",
    ),
    # Thanks.
    (
        re.compile(r"\b(gracias|thanks|thx|gracia)\b", re.IGNORECASE),
        "pray",
    ),
    # Analysis / inspection. Catches: "analiza", "revisa", "chequea",
    # "lee", "estudia", "mira el", "look at".
    (
        re.compile(
            r"\b(analiz|revis|chequ|estudi|investig|examin|le[eé]r?|"
            r"reading|look\s+at|mir[aá]\s)",
            re.IGNORECASE,
        ),
        "eyes",
    ),
    # Scheduling. "todos los lunes", "cada hora", "recordatorio", "cron".
    (
        re.compile(
            r"\b(todos\s+los|cada\s+(hora|d[ií]a|semana|mes|lunes|martes|"
            r"mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|minuto)|"
            r"recordatorio|recordame|recuerdame|programa[rd]?|schedule|"
            r"cron)\b",
            re.IGNORECASE,
        ),
        "alarm_clock",
    ),
    # Send a message / DM to someone. Prefix-based (no trailing \b) so we
    # catch the Spanish leísmo / pronominal forms ("mandale", "mándame",
    # "envíaselo") plus English send / dile / escribi. Comes BEFORE reports
    # so "mándame el reporte" goes to the verb (envelope) rather than the
    # object (chart) -- the user is asking for an action, not a metric.
    (
        re.compile(
            r"\b(env[ií]a|envia|m[aá]nda|send|dile|escrib)",
            re.IGNORECASE,
        ),
        "envelope_with_arrow",
    ),
    # Reports / metrics / dashboards.
    (
        re.compile(
            r"\b(reporte?s?|analytics|m[eé]trica|stats|dashboard|kpi|"
            r"resumen|summary)\b",
            re.IGNORECASE,
        ),
        "bar_chart",
    ),
    # Delete / destroy / clean up. Same prefix style for the same reason
    # ("borrale", "elimínalo", "quítaselo").
    (
        re.compile(
            r"\b(borr|delet|elimin|remove|quit|limpia)",
            re.IGNORECASE,
        ),
        "wastebasket",
    ),
    # Build / create / generate.
    (
        re.compile(
            r"\b(cre[aá]|crear|armar?|build|implementa[rd]?|gener[ae]r|"
            r"design|construi[rd]|monta[rd]?)\b",
            re.IGNORECASE,
        ),
        "hammer_and_wrench",
    ),
    # Help / confusion.
    (
        re.compile(
            r"\b(ayuda|help|no\s+entiendo|c[oó]mo\b|how\s+do|"
            r"how\s+can|por\s+qu[eé])\b",
            re.IGNORECASE,
        ),
        "thinking_face",
    ),
]


def pick_reaction(text: str | None) -> str:
    """Map the user's message text to a single Slack emoji shortname.

    Falls back to DEFAULT_REACTION (hourglass) when the text is empty or
    no rule matches. Returns the shortname WITHOUT colons -- callers pass
    it directly to `reactions.add(name=...)`.
    """
    if not text:
        return DEFAULT_REACTION
    for pattern, emoji in _RULES:
        if pattern.search(text):
            return emoji
    return DEFAULT_REACTION


__all__ = ["pick_reaction", "DEFAULT_REACTION"]
