"""Autonomous fact extraction post-pass (slice T-X Phase B).

After every agent turn the runner spawns a fire-and-forget task that
calls this module. It sends the (user message, agent response) pair to
a cheap model, asks it to extract durable facts, and appends each fact
to the appropriate memory skill -- bypassing the `remember` agent tool
since this isn't a tool call from the main loop.

Design notes:

- **Cost vs coverage**: Sam chose "cada turno" (every turn). At haiku
  prices that's ~$0.001 per turn. In a busy workspace (100 turns/day)
  that's ~$0.10/day per workspace -- meaningful but not prohibitive.
  We may revisit with sampling later if telemetry shows most turns
  contribute zero facts.

- **Fire-and-forget**: never blocks the user-visible response. Errors
  are logged, no retries, no surfacing to the user. If the haiku call
  drops, we miss a turn's worth of facts -- acceptable given Phase C
  compaction smooths over duplicates and the user can always say
  "recordá que X" to force a write via the `remember` tool.

- **Scope selection**: the model picks `user|team|company` per fact.
  We trust it the same way we trust the `remember` tool's scope
  parameter. If it picks `user`, we route to the calling user's
  `users/<id>` skill; if it picks `team` or `company`, to the
  workspace-scoped slugs.

- **Source tag**: `post-pass` (already in append's SourceTag literal).
  Distinguishes auto-extracted facts from explicit `remember` calls
  in the observation log -- useful when tuning compaction.

- **Conservative bar**: the prompt explicitly tells the model to err
  on the side of NOT extracting. We want high precision (no garbage
  facts), not high recall (catch everything). Compaction can't undo
  a bad fact; an empty turn is fine.

The function never raises out.
"""

from __future__ import annotations

import json
import re
import uuid

import litellm
import structlog
from langfuse import get_client

from app.config import get_settings
from app.memory import append
from app.memory.constants import COMPANY_SLUG, TEAM_SLUG, user_slug

log = structlog.get_logger(__name__)
_langfuse = get_client()


# Bound the prompt size so a huge agent response (e.g. a long code dump)
# doesn't balloon the haiku call. The most-recent ~4000 chars of each
# side are enough for the durable-fact signal.
MAX_TURN_CHARS_PER_SIDE: int = 4_000

# Hard cap on extracted facts per turn. Sets an upper bound on append calls
# so a model that returns 50 "facts" doesn't spam the observation log.
MAX_FACTS_PER_TURN: int = 5


_POST_PASS_PROMPT = """\
Tarea: extraer hechos DURABLES de UN intercambio entre un usuario y un
asistente de IA en Slack. Esto NO es una respuesta al usuario; es un
proceso interno de aprendizaje.

CONTEXTO:

<user_message>
{user_text}
</user_message>

<assistant_response>
{agent_response}
</assistant_response>

INSTRUCCIONES:

1. Devolvé un JSON array de objetos. Cada objeto tiene:
     - "scope": uno de "user", "team", "company"
     - "fact": string declarativo, <250 caracteres, en español
2. Máximo {max_facts} objetos.
3. Scopes:
     - "user"     hechos sobre el usuario que está chateando (idioma,
                  herramientas que usa, rol, preferencias, background).
     - "team"     hechos sobre OTRAS personas del workspace, canales,
                  estructura organizacional.
     - "company"  hechos sobre la empresa misma: producto, mercado,
                  stack, integraciones, stage.
4. CRITERIOS PARA INCLUIR un hecho:
     - El hecho seguirá siendo verdad dentro de un mes.
     - El usuario lo afirmó claramente (no especulación del asistente).
     - Es ESPECÍFICO ("Sam usa Folk CRM"), no general ("Sam usa un CRM").
     - Aporta valor para que el asistente entienda mejor al usuario o
       al workspace en futuras conversaciones.
5. CRITERIOS PARA EXCLUIR:
     - Cualquier estado efímero ("Sam está cansado", "hoy hay daily").
     - Opiniones / sentimientos sin contexto durable.
     - Información ya implícita en el rol del asistente.
     - Detalles de un ticket / PR / mensaje específico.
     - Cuando dudás, NO incluyas. La precisión importa más que la cobertura.
6. Si no hay hechos durables en el turno, devolvé EXACTAMENTE: []

OUTPUT: solo el JSON array. Sin preámbulo, sin código fenced, sin
comentarios. Si no hay hechos, devolvé exactamente: []
"""


def _trim(text: str, cap: int = MAX_TURN_CHARS_PER_SIDE) -> str:
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    return text[:cap].rstrip() + "…"


async def _extract(user_text: str, agent_response: str) -> list[dict]:
    """One haiku call. Returns the parsed list of {scope, fact} objects.
    On any failure / unparseable output, returns []."""
    if not (user_text or "").strip() and not (agent_response or "").strip():
        return []
    prompt = _POST_PASS_PROMPT.format(
        user_text=_trim(user_text) or "(vacío)",
        agent_response=_trim(agent_response) or "(vacío)",
        max_facts=MAX_FACTS_PER_TURN,
    )
    settings = get_settings()
    model = settings.cheap_model
    try:
        with _langfuse.start_as_current_observation(
            as_type="generation",
            name="memory:post-pass",
            model=model,
            input=prompt,
        ) as gen:
            response = await litellm.acompletion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
            )
            raw = (response.choices[0].message.content or "").strip()
            usage = getattr(response, "usage", None)
            if usage is not None:
                gen.update(
                    output=raw,
                    usage_details={
                        "input": getattr(usage, "prompt_tokens", 0) or 0,
                        "output": getattr(usage, "completion_tokens", 0) or 0,
                    },
                )
            else:
                gen.update(output=raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("post_pass_model_failed", error=str(exc)[:200])
        return []

    raw = re.sub(r"^```(?:\w+)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw).strip()
    if not raw or raw == "[]":
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.info("post_pass_unparseable", sample=raw[:200])
        return []
    if not isinstance(parsed, list):
        return []

    cleaned: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        scope = item.get("scope")
        fact = item.get("fact")
        if scope not in ("user", "team", "company"):
            continue
        if not isinstance(fact, str) or not fact.strip():
            continue
        cleaned.append({"scope": scope, "fact": fact.strip()[:480]})
        if len(cleaned) >= MAX_FACTS_PER_TURN:
            break
    return cleaned


async def extract_and_persist(
    *,
    workspace_id: uuid.UUID,
    slack_user_id: str,
    user_text: str,
    agent_response: str,
) -> dict[str, int]:
    """Single post-pass: extract facts from the turn, append each to the
    right memory skill. Returns a small count dict for logging.

    Best-effort: never raises. Safe to invoke via `asyncio.create_task`
    from the post-completion path in the runner.
    """
    counts = {"facts_extracted": 0, "facts_written": 0}
    try:
        facts = await _extract(user_text or "", agent_response or "")
    except Exception as exc:  # noqa: BLE001
        log.warning("post_pass_extract_failed", error=str(exc)[:200])
        return counts
    counts["facts_extracted"] = len(facts)
    if not facts:
        return counts

    for entry in facts:
        scope = entry["scope"]
        fact = entry["fact"]
        if scope == "user":
            skill_name = user_slug(slack_user_id)
        elif scope == "team":
            skill_name = TEAM_SLUG
        else:
            skill_name = COMPANY_SLUG
        try:
            ok = await append.append_observation(
                workspace_id, skill_name, text=fact, source="post-pass"
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "post_pass_append_failed",
                workspace_id=str(workspace_id),
                skill_name=skill_name,
                error=str(exc)[:200],
            )
            continue
        if ok:
            counts["facts_written"] += 1

    if counts["facts_written"]:
        log.info(
            "post_pass_observations_persisted",
            workspace_id=str(workspace_id),
            slack_user_id=slack_user_id,
            **counts,
        )
    return counts


__all__ = ["extract_and_persist", "MAX_FACTS_PER_TURN", "MAX_TURN_CHARS_PER_SIDE"]
