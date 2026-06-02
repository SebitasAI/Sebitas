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
    # Optional context for promise extraction (Phase 2). When all four
    # are present, the post-pass ALSO extracts user promises and creates
    # follow-ups. When any are missing (older callers, system runs),
    # only the fact extraction runs.
    app_user_id: uuid.UUID | None = None,
    channel: str | None = None,
    conversation_key: str | None = None,
    reply_thread_ts: str | None = None,
    run_id: str | None = None,
) -> dict[str, int]:
    """Single post-pass: extract facts from the turn, append each to the
    right memory skill. Returns a small count dict for logging.

    Best-effort: never raises. Safe to invoke via `asyncio.create_task`
    from the post-completion path in the runner.

    When the optional thread context args are passed, ALSO extracts
    promises from the user message and schedules follow-ups. Facts and
    promises run as TWO separate haiku calls -- isolating failure
    modes and keeping the existing fact-extraction shape stable.
    """
    counts = {
        "facts_extracted": 0,
        "facts_written": 0,
        "promises_extracted": 0,
        "promises_scheduled": 0,
    }
    try:
        facts = await _extract(user_text or "", agent_response or "")
    except Exception as exc:  # noqa: BLE001
        log.warning("post_pass_extract_failed", error=str(exc)[:200])
        facts = []
    counts["facts_extracted"] = len(facts)

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

    # Promise extraction + follow-up scheduling. Skipped when the
    # caller didn't pass thread context (older paths, system runs).
    if (
        app_user_id is not None
        and channel
        and conversation_key
    ):
        try:
            promises = await _extract_promises(user_text or "", agent_response or "")
        except Exception as exc:  # noqa: BLE001
            log.warning("post_pass_promises_failed", error=str(exc)[:200])
            promises = []
        counts["promises_extracted"] = len(promises)

        if promises:
            # Lazy import to avoid pulling follow_ups into the memory
            # module's mandatory dependency surface (helps test isolation).
            from app.follow_ups import repository as fu_repo

            for entry in promises:
                action = entry["action"]
                wait_hours = entry["wait_hours"]
                # The follow-up reason references the user's commitment so
                # the dispatched nudge reads naturally: "Oye, dijiste que
                # ibas a X, ¿lo pudiste hacer?"
                reason = f"el user prometió: {action}"
                # Dedup key prefixed so a normal `schedule_follow_up` from
                # this same run can coexist without collision.
                dedup_key = f"promise:{run_id}" if run_id else None
                new_id = await fu_repo.create_follow_up(
                    workspace_id=workspace_id,
                    app_user_id=app_user_id,
                    channel=channel,
                    conversation_key=conversation_key,
                    reply_thread_ts=reply_thread_ts,
                    reason=reason,
                    wait_hours=wait_hours,
                    created_by_run_id=dedup_key,
                )
                if new_id is not None:
                    counts["promises_scheduled"] += 1

    if counts["facts_written"] or counts["promises_scheduled"]:
        log.info(
            "post_pass_observations_persisted",
            workspace_id=str(workspace_id),
            slack_user_id=slack_user_id,
            **counts,
        )
    return counts


# --------------------------------------------------------------------------- #
# Promise extraction (Phase 2)
# --------------------------------------------------------------------------- #
#
# The user sometimes commits to doing something in a future turn:
#   "te mando el spec mañana"
#   "voy a revisar eso esta tarde"
#   "le pregunto a Laura el lunes y te aviso"
#
# This sub-pass scans the same turn for those commitments and schedules
# a follow-up so Misterr pings if the user doesn't come through. Same
# bias as the facts extraction: high precision over recall. False
# positives here are MORE annoying than missed promises because every
# false positive becomes a nudge the user has to dismiss.
#
# Separate haiku call rather than overloading the facts prompt: cleaner
# isolation, can be disabled independently, doesn't change the existing
# fact-extraction shape.

# Hard cap. At most one promise per turn; multiple promises in a single
# message tend to be either a list ("voy a hacer X, Y, Z") that the user
# really treats as one block, OR the model overreaching.
MAX_PROMISES_PER_TURN: int = 1

# Bounds on the wait_hours we'll honor from the model. Tight to limit
# blast radius of any hallucination.
PROMISE_MIN_WAIT_HOURS: int = 4
PROMISE_MAX_WAIT_HOURS: int = 168  # one week


_PROMISE_EXTRACTION_PROMPT = """\
Tarea: detectar si el USER (no el asistente) hizo una PROMESA o COMPROMISO
concreto de hacer algo en un futuro próximo. Esto es un proceso interno
para que el asistente pueda recordarle más tarde si no cumple.

CONTEXTO:

<user_message>
{user_text}
</user_message>

<assistant_response>
{agent_response}
</assistant_response>

INSTRUCCIONES:

1. Devolvé un JSON array con MÁXIMO {max_promises} objeto. Cada objeto:
     - "action": qué prometió hacer el USER (string corto, en español, <200 chars)
     - "wait_hours": en cuántas horas chequear si lo hizo (integer entre {min_h} y {max_h})
2. Una "promesa" es algo que cumple TODOS estos criterios:
     - El user dijo explícitamente que LO VA A HACER ÉL (no que "alguien debería" o "estaría bueno").
     - El action es CONCRETO (puede observarse si cumplió o no): "te mando el spec", "le pregunto a Laura", "envio el reporte". NO: "voy a pensarlo", "voy a reflexionar".
     - Tiene un horizonte temporal razonable: hoy, mañana, esta semana, próximo lunes. Inferí wait_hours del lenguaje.
3. NO ES una promesa si:
     - El user habla de algo que YA HIZO ("ya le pregunté a Laura").
     - El user pregunta si DEBERÍA hacerlo ("debería mandar el spec?").
     - El user describe el plan de OTRO ("Laura va a mandarlo").
     - Es una intención difusa sin acción concreta ("voy a ver", "voy a pensarlo").
     - El asistente PIDIÓ algo y el user respondió "sí" sin decir cuándo. (Eso ya se cubre con `schedule_follow_up`.)
4. wait_hours guía:
     - "ahora", "en un toque", "ya": 4
     - "hoy", "esta tarde": 8
     - "mañana": 24
     - "esta semana": 48
     - "próximo lunes" / "la semana que viene": 72-168 (inferí del día actual si podés)
5. Si DUDÁS, NO incluyas. Mejor perder una promesa que crear un nudge molesto.
6. Si no hay promesas, devolvé EXACTAMENTE: []

OUTPUT: solo el JSON array. Sin preámbulo, sin código fenced, sin
comentarios. Si no hay promesas, devolvé exactamente: []
"""


async def _extract_promises(user_text: str, agent_response: str) -> list[dict]:
    """One haiku call. Returns the parsed list of {action, wait_hours}
    objects. Returns [] on any failure / unparseable output.

    Independent from `_extract`: the prompt is narrower, the validation
    is stricter, and a failure here doesn't affect facts persistence."""
    if not (user_text or "").strip():
        return []
    prompt = _PROMISE_EXTRACTION_PROMPT.format(
        user_text=_trim(user_text) or "(vacío)",
        agent_response=_trim(agent_response) or "(vacío)",
        max_promises=MAX_PROMISES_PER_TURN,
        min_h=PROMISE_MIN_WAIT_HOURS,
        max_h=PROMISE_MAX_WAIT_HOURS,
    )
    settings = get_settings()
    model = settings.cheap_model
    try:
        with _langfuse.start_as_current_observation(
            as_type="generation",
            name="memory:post-pass:promises",
            model=model,
            input=prompt,
        ) as gen:
            response = await litellm.acompletion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
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
        log.warning("post_pass_promises_model_failed", error=str(exc)[:200])
        return []

    raw = re.sub(r"^```(?:\w+)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw).strip()
    if not raw or raw == "[]":
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.info("post_pass_promises_unparseable", sample=raw[:200])
        return []
    if not isinstance(parsed, list):
        return []

    cleaned: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        wait_hours = item.get("wait_hours")
        if not isinstance(action, str) or not action.strip():
            continue
        try:
            wait_hours = int(wait_hours)
        except (TypeError, ValueError):
            continue
        if not (PROMISE_MIN_WAIT_HOURS <= wait_hours <= PROMISE_MAX_WAIT_HOURS):
            continue
        cleaned.append({"action": action.strip()[:480], "wait_hours": wait_hours})
        if len(cleaned) >= MAX_PROMISES_PER_TURN:
            break
    return cleaned


__all__ = [
    "extract_and_persist",
    "MAX_FACTS_PER_TURN",
    "MAX_TURN_CHARS_PER_SIDE",
    "MAX_PROMISES_PER_TURN",
    "PROMISE_MIN_WAIT_HOURS",
    "PROMISE_MAX_WAIT_HOURS",
]
