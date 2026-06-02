"""Auto-improve for integration skills.

After every agent turn that included one or more `run_action` calls,
this module runs a cheap-model post-pass that looks at:

  - what the user wanted (`user_text`)
  - what the agent replied (`agent_response`)
  - which (app, action_id) the agent invoked

…and asks: "Is there a generalizable lesson here that would help the
agent pick a better action next time?" If yes, the lesson is appended
to the `## Usage notes` section of the `integrations/<app>` skill
(preserved across the daily Pipedream-catalog refresh).

Guard rails so we don't poison skills with noise:

  - **Prompt biased to NULL.** The haiku prompt explicitly says "default
    to no insight if uncertain". One observation per app per turn max.
  - **Append-only.** We never edit or delete existing notes.
  - **Hard cap.** Up to `MAX_USAGE_NOTES` (20) per skill. Beyond that the
    oldest dated entries are dropped (silently — admins can re-emit).
  - **Same-line dedup.** If a new insight matches verbatim an existing
    one, we drop it instead of appending.

Failure modes are intentionally silent: any exception short-circuits the
post-pass for this turn. The agent's user-facing reply is already posted
by the time we run.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone

import litellm
import structlog
from langfuse import get_client
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Skill
from app.db.session import get_session
from app.integrations.catalog_skills import (
    MAX_BODY_BYTES,
    SECTION_USAGE,
    _slug_for,
)
from app.skills import registry as skill_registry
from app.skills import storage as skill_storage

log = structlog.get_logger(__name__)
_langfuse = get_client()


# Hard limit on insights per skill before we start dropping old ones.
MAX_USAGE_NOTES: int = 20

# Max length of a single insight, post-trim. The haiku prompt asks for
# 1-2 sentences but the model occasionally exceeds; we truncate rather
# than reject to avoid losing useful signal.
MAX_INSIGHT_CHARS: int = 320


_INSIGHT_PROMPT = """\
Tarea: revisar UN turno entre un usuario y un asistente AI en Slack en
el cual el asistente usó la integración `{app}` mediante una o más
acciones. Tu objetivo es detectar un ERROR de PARÁMETROS o ELECCIÓN de
action concreto y emitir guidance para corregirlo en el futuro.

CONTEXTO:

<user_request>
{user_text}
</user_request>

<assistant_response>
{agent_response}
</assistant_response>

<actions_invoked>
{actions_detailed}
</actions_invoked>

INSTRUCCIONES:

1. Devolvé un JSON con DOS campos:
     - "has_insight": boolean
     - "insight": string en español, ≤ {max_chars} chars

2. Buscás SOLO uno de estos tipos de errores. Si no encajan, devolvé
   `has_insight: false`:

   **A) PARAM ERROR** (más importante): el asistente eligió la action
   correcta pero seteó un parámetro CRÍTICO mal. Ejemplos:
     - boolean flag `include*` en `false` cuando el intent del user
       requería los datos incluidos (e.g. `includeParties=false` cuando
       el user buscaba por nombre de empresa)
     - filtro de fecha demasiado estrecho cuando el user no lo limitó
     - `maxResults` muy bajo perdiendo data
     - parámetro requerido omitido → la action devolvió data vacía
   El insight debe nombrar el PARAM ESPECÍFICO y el VALOR CORRECTO:
   "Cuando el user pida filtrar calls por empresa/cuenta, set
   `includeParties=true` en gong-get-extensive-data y filtrá
   client-side por parties[].name."

   **B) WRONG ACTION**: el asistente eligió una action peor cuando
   existía una mejor en el catálogo. El insight debe nombrar AMBAS
   actions y explicar cuándo cambiar:
   "Cuando el intent es filtrar por nombre, NO uses list-X (solo trae
   ids), usá get-extensive-X con los filtros adecuados."

3. CRITERIOS PARA emitir NULL (`has_insight: false`):
     - El turno fue un éxito directo (no hay error que corregir).
     - El error fue de la API misma, no del asistente.
     - El asistente respondió pidiendo aclaración (eso es OK, no es
       un error de uso de la integración -- NO digas "pedir filtros
       primero" porque eso no resuelve el bug, solo lo posterga).
     - La causa del fallo es ambigua y no podés señalar un param
       específico o una action específica.
     - Cuando dudás. La precisión importa más que la cobertura.

4. PROHIBIDO:
     - Notas tipo "pedir más contexto al user" o "pedir filtros antes
       de invocar" (no son insights, son re-pasar el problema).
     - Notas genéricas ("optimizar la búsqueda", "evitar costo").
     - Notas sin evidencia clara en el turno.
     - El caso es muy específico al user / workspace / fecha.

4. NO inventes recomendaciones que no estén respaldadas por evidencia
   en el turno. Si la única evidencia es "el agente se equivocó",
   pero no podés decir POR QUÉ se equivocó ni QUÉ debería haber hecho,
   devolvé `has_insight: false`.

OUTPUT: solo el JSON. Sin preámbulo, sin código fenced, sin comentarios.
Cuando `has_insight: false`, podés omitir el campo `insight` o ponerlo en
"".
"""


def _trim(text: str, cap: int = 4000) -> str:
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    return text[:cap].rstrip() + "…"


async def _extract_insight(
    user_text: str,
    agent_response: str,
    app: str,
    action_calls: list[dict],
) -> str | None:
    """One haiku call. Returns the insight string when emitted, else
    None. Never raises.

    `action_calls` is a list of `{action_id, params}` dicts so the
    haiku can see PARAM-level decisions (not just which action was
    chosen). This is what unlocks PARAM-error insights like
    `includeParties=false` being wrong for company-filter intents."""
    if not action_calls:
        return None
    detailed_lines: list[str] = []
    for c in action_calls[:10]:
        aid = c.get("action_id") or "?"
        params = c.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        # Format each param compactly. Booleans + small primitives
        # are most informative; trim long values to keep the prompt
        # focused.
        if params:
            param_bits = []
            for k, v in list(params.items())[:15]:
                if isinstance(v, (str, int, float, bool)):
                    val = str(v)[:60]
                else:
                    val = "[…]"  # collapse objects/lists for brevity
                param_bits.append(f"{k}={val}")
            params_repr = ", ".join(param_bits)
        else:
            params_repr = "(no params passed)"
        detailed_lines.append(f"- `{aid}` called with: {params_repr}")
    actions_detailed = "\n".join(detailed_lines)

    prompt = _INSIGHT_PROMPT.format(
        app=app,
        user_text=_trim(user_text) or "(vacío)",
        agent_response=_trim(agent_response) or "(vacío)",
        actions_detailed=actions_detailed,
        max_chars=MAX_INSIGHT_CHARS,
    )
    settings = get_settings()
    model = settings.cheap_model
    try:
        with _langfuse.start_as_current_observation(
            as_type="generation",
            name="integration:auto-improve",
            model=model,
            input=prompt,
        ) as gen:
            response = await litellm.acompletion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
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
        log.info("auto_improve_model_failed", app=app, error=str(exc)[:200])
        return None

    raw = re.sub(r"^```(?:\w+)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw).strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.info("auto_improve_unparseable", app=app, sample=raw[:200])
        return None
    if not isinstance(parsed, dict):
        return None
    if not parsed.get("has_insight"):
        return None
    insight = parsed.get("insight")
    if not isinstance(insight, str) or not insight.strip():
        return None
    insight = insight.strip().replace("\n", " ")
    if len(insight) > MAX_INSIGHT_CHARS:
        insight = insight[:MAX_INSIGHT_CHARS].rstrip() + "…"
    return insight


def _bullets_in_usage_section(body: str) -> tuple[int, int, list[str]]:
    """Locate the `## Usage notes` section. Returns
    (section_start_idx, section_end_idx, existing_bullets)."""
    idx = body.find(SECTION_USAGE)
    if idx == -1:
        return -1, -1, []
    section_after_header = body[idx + len(SECTION_USAGE):]
    # Section ends at the next H2 (or EOF).
    end_match = re.search(r"\n## ", section_after_header)
    end_idx = (
        idx + len(SECTION_USAGE) + end_match.start()
        if end_match
        else len(body)
    )
    section_text = body[idx + len(SECTION_USAGE):end_idx]
    bullets = [
        line.strip()
        for line in section_text.splitlines()
        if line.strip().startswith("- ")
    ]
    return idx, end_idx, bullets


def _build_usage_note_line(insight: str, when: datetime) -> str:
    ts = when.strftime("%Y-%m-%d")
    return f"- {ts} [auto-improve]: {insight}"


async def _append_usage_note(workspace_id: uuid.UUID, app: str, insight: str) -> bool:
    """Append a single bullet to the `## Usage notes` section of
    `integrations/<app>`. Cap at MAX_USAGE_NOTES. Returns True on a
    successful write; False on any silent failure (skill missing,
    dedup hit, body too large, optimistic-lock conflict)."""
    slug = _slug_for(app)
    async with get_session() as session:
        skill = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace_id,
                    Skill.name == slug,
                )
            )
        ).scalar_one_or_none()
    if skill is None:
        log.info(
            "auto_improve_skill_missing",
            workspace_id=str(workspace_id), app=app,
        )
        return False

    try:
        body = await skill_storage.download_skill_body(
            workspace_id=skill.workspace_id,
            skill_id=skill.id,
            version=skill.version,
            r2_ref=skill.body_r2_ref,
        )
    except Exception as exc:  # noqa: BLE001
        log.info(
            "auto_improve_body_read_failed",
            app=app, error=str(exc)[:200],
        )
        return False

    start_idx, end_idx, existing = _bullets_in_usage_section(body)
    if start_idx == -1:
        # No Usage notes section (corrupted skill). Don't try to
        # recreate the structure here -- let the next catalog refresh
        # rebuild the skill from scratch.
        log.info("auto_improve_section_missing", app=app)
        return False

    new_line = _build_usage_note_line(insight, datetime.now(timezone.utc))

    # Dedup: same exact text already present.
    for line in existing:
        if line.split("]: ", 1)[-1].strip().lower() == insight.strip().lower():
            return False

    # Cap: keep only the latest (MAX_USAGE_NOTES - 1) so we have room
    # for the new one. We drop OLDEST since newer insights reflect
    # more recent learnings.
    kept = existing[-(MAX_USAGE_NOTES - 1):]
    new_section = SECTION_USAGE + "\n\n" + "\n".join(kept + [new_line]) + "\n"
    new_body = body[:start_idx] + new_section + body[end_idx:]

    new_size = len(new_body.encode("utf-8"))
    if new_size > MAX_BODY_BYTES:
        log.warning(
            "auto_improve_body_cap_hit", app=app, size=new_size,
        )
        return False

    try:
        await skill_registry.update_skill_body(
            skill_id=skill.id, new_body=new_body, new_size_bytes=new_size
        )
    except Exception as exc:  # noqa: BLE001
        log.info(
            "auto_improve_write_failed",
            app=app, error=str(exc)[:200],
        )
        return False

    log.info(
        "auto_improve_appended",
        workspace_id=str(workspace_id),
        app=app,
        note=insight[:100],
    )
    return True


# Deterministic iteration-loop detector. When the agent calls the
# same (app, action_id) >= this threshold within one turn AND never
# passes `filter_substring`, the auto-improve appends a fixed note
# pointing the agent at `filter_substring` for needle searches. This
# bypasses the haiku call entirely (no LLM cost, deterministic),
# fires same-day even if the haiku declines to emit an insight, and
# self-reinforces because the note lives in the skill body that
# future runs reload via `load_skill`.
_ITERATION_LOOP_THRESHOLD: int = 3


def _detect_iteration_loop(action_calls: list[dict]) -> str | None:
    """Return a deterministic insight when the agent iterated the
    same action >= threshold in one turn without using
    `filter_substring`. None otherwise. The insight names the
    specific action and the right alternative."""
    if not action_calls:
        return None
    by_action: dict[str, int] = {}
    used_filter: set[str] = set()
    for c in action_calls:
        aid = c.get("action_id")
        if not isinstance(aid, str) or not aid:
            continue
        by_action[aid] = by_action.get(aid, 0) + 1
        fs = c.get("filter_substring")
        if isinstance(fs, str) and fs.strip():
            used_filter.add(aid)
    looped = [
        aid for aid, n in by_action.items()
        if n >= _ITERATION_LOOP_THRESHOLD and aid not in used_filter
    ]
    if not looped:
        return None
    # If multiple actions looped, the note names the first
    # alphabetically for stable dedup; the lesson generalizes anyway.
    aid = sorted(looped)[0]
    return (
        f"Cuando necesites encontrar un item específico por nombre o "
        f"entidad (empresa, persona, email, cuenta) dentro de `{aid}`, "
        "pasa `filter_substring='<needle>'` en una sola llamada en lugar "
        "de iterar varias veces con ventanas de fecha distintas. El "
        "filtro hace deep-match case-insensitive sobre todos los campos "
        "anidados de cada item y devuelve solo los que matchean. Una "
        "sola tool call. Ejemplo: "
        f"`run_action(app=..., action_id='{aid}', params=..., "
        "filter_substring='MercadoLibre')`."
    )


async def maybe_improve_skill(
    *,
    workspace_id: uuid.UUID,
    user_text: str,
    agent_response: str,
    integration_calls: list[dict],
) -> int:
    """Entry point from the runner's post-completion path. Best-effort;
    returns the number of insights appended (often 0)."""
    if not integration_calls:
        return 0
    # Group calls (action_id + params + filter_substring) by app so
    # we make ONE haiku call per app touched in this turn. Passing
    # params lets haiku see param-level mistakes like
    # `includeParties=false`; passing filter_substring lets the
    # deterministic detector check for iteration loops.
    by_app: dict[str, list[dict]] = {}
    for call in integration_calls:
        app = (call.get("app") or "").lower().strip()
        if not app:
            continue
        aid = call.get("action_id") or call.get("component_id")
        if not isinstance(aid, str) or not aid:
            continue
        by_app.setdefault(app, []).append({
            "action_id": aid,
            "params": call.get("params") or {},
            "filter_substring": call.get("filter_substring"),
        })

    written = 0
    for app, action_calls in by_app.items():
        # 1. Deterministic check: iteration loop without filter_substring.
        # This is free, immediate, and reinforces the right pattern
        # even when haiku declines to emit anything.
        loop_insight = _detect_iteration_loop(action_calls)
        if loop_insight:
            try:
                if await _append_usage_note(workspace_id, app, loop_insight):
                    log.info(
                        "auto_improve_iteration_loop_note",
                        app=app,
                        action_calls=len(action_calls),
                    )
                    written += 1
            except Exception as exc:  # noqa: BLE001
                log.info(
                    "auto_improve_iteration_loop_failed",
                    app=app, error=str(exc)[:200],
                )

        # 2. LLM-driven check: param errors / wrong action.
        try:
            insight = await _extract_insight(
                user_text, agent_response, app, action_calls
            )
        except Exception as exc:  # noqa: BLE001
            log.info("auto_improve_unexpected", app=app, error=str(exc)[:200])
            continue
        if not insight:
            continue
        if await _append_usage_note(workspace_id, app, insight):
            written += 1
        # Yield between apps.
        await asyncio.sleep(0)
    return written


__all__ = [
    "maybe_improve_skill",
    "MAX_USAGE_NOTES",
    "MAX_INSIGHT_CHARS",
]
