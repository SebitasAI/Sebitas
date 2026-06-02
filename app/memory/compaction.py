"""Periodic compaction of memory-skill bodies (slice T-X Phase C).

Bodies are append-only by design (see `app/memory/append.py`). Without
compaction they grow until they hit the 200KB cap, at which point
`append_observation` starts silently dropping observations
(`memory_append_body_capped`). The compaction loop reads each memory
skill body, asks the cheap model to rewrite the `## Curated summary`
integrating the `## Observations log`, then clears the log.

Eligibility: a body compacts when EITHER:
  - it is over `BODY_SIZE_THRESHOLD` bytes, OR
  - its log has over `OBSERVATIONS_THRESHOLD` bullets.

Skills below both thresholds are skipped -- a haiku call to rewrite the
same curated text would just spend tokens for no information gain.

Race conditions: `append` is optimistic-locked on `skill.version`.
Compaction also goes through `update_skill_body`, which bumps version.
If an append lands after we read the body and before we write back, the
write will fail; we log + skip this round. The next cycle picks up the
larger body. We do NOT retry inline because:
  1. The window is small (one haiku call + R2 upload, a few seconds).
  2. Retrying means re-spending the haiku tokens.
  3. Missing a cycle is fine -- the body is still under cap; we catch up.

This module never raises out to its caller. Per-skill errors are logged
and skipped; the loop continues with the next skill / workspace.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone

import litellm
import structlog
from langfuse import get_client
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Skill, Workspace
from app.db.session import get_session
from app.skills import registry as skill_registry
from app.skills import storage as skill_storage

log = structlog.get_logger(__name__)
_langfuse = get_client()


# A body is eligible for compaction at the lower of these two thresholds.
# Both are deliberately well below MAX_BODY_BYTES (200KB) and the per-
# observation cap, so compaction kicks in *before* appends start failing.
BODY_SIZE_THRESHOLD: int = 50_000  # bytes
OBSERVATIONS_THRESHOLD: int = 50   # bullets in `## Observations log`

# Hard ceiling on the new curated summary the model is allowed to produce.
# 4000 chars (~1000 tokens) keeps the body small enough that even after
# many compaction cycles the prompt remains affordable. If the model
# returns more we truncate -- a slight loss of detail beats a runaway body.
MAX_CURATED_CHARS: int = 4_000

# Cadence of the background loop. 24h is a balance between "wakes up fast
# enough to keep up with chatty workspaces" and "doesn't waste haiku tokens
# on workspaces that barely grew." Override via `MEMORY_COMPACTION_INTERVAL_SECONDS`
# at startup if needed for debugging.
DEFAULT_INTERVAL_SECONDS: int = 24 * 60 * 60


_CURATED_HEADER = "## Curated summary"
_LOG_HEADER = "## Observations log"


def _split_sections(body: str) -> tuple[str, list[str], str]:
    """Return (curated_text, log_bullets, leading_metadata).

    `leading_metadata` is anything before `## Curated summary` (HTML comments
    written by the seeder, e.g. `<!-- bootstrapped: ... -->`). We preserve it
    verbatim so the bootstrap timestamp stays in the body forever.

    `curated_text` is the body of the `## Curated summary` section (without
    the header line).

    `log_bullets` is each bullet from `## Observations log`. We keep the raw
    line including the leading `-` so the LLM sees them in their canonical
    shape.

    If a section is missing (corrupt body, hand-edit), the missing piece is
    returned as an empty string / empty list. We don't raise here -- compaction
    is best-effort; a partly-broken body is still better than no body.
    """
    curated_idx = body.find(_CURATED_HEADER)
    log_idx = body.find(_LOG_HEADER)

    if curated_idx == -1:
        # No curated section at all. Treat the entire body as leading metadata
        # and let the LLM build a curated summary from scratch (log_bullets
        # may also be empty here).
        leading = body if log_idx == -1 else body[:log_idx]
        curated = ""
    else:
        leading = body[:curated_idx]
        curated_end = log_idx if log_idx != -1 and log_idx > curated_idx else len(body)
        # Skip the header line itself.
        curated_section = body[curated_idx + len(_CURATED_HEADER):curated_end]
        curated = curated_section.strip()

    bullets: list[str] = []
    if log_idx != -1:
        log_section = body[log_idx + len(_LOG_HEADER):]
        for raw in log_section.splitlines():
            line = raw.strip()
            if line.startswith("- "):
                bullets.append(line)
            # Lines that aren't bullets (empty lines, stray text) are ignored.
            # Compaction will rebuild the section cleanly.

    return curated, bullets, leading


def _build_compacted_body(*, leading_metadata: str, new_curated: str) -> str:
    """Rebuild a memory body from a freshly-compacted curated summary. The
    observations log is emptied -- new observations after this point go to
    a fresh log section."""
    when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts: list[str] = []
    if leading_metadata.strip():
        parts.append(leading_metadata.rstrip())
        parts.append("")
    parts.append(f"<!-- last_compacted: {when} -->")
    parts.append(_CURATED_HEADER)
    parts.append(new_curated.strip())
    parts.append("")
    parts.append(_LOG_HEADER)
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _is_eligible(body: str) -> tuple[bool, str]:
    """Return (eligible, reason). Reason is a short tag for logs."""
    size = len(body.encode("utf-8"))
    if size > BODY_SIZE_THRESHOLD:
        return True, f"body_size:{size}"
    _, bullets, _ = _split_sections(body)
    if len(bullets) > OBSERVATIONS_THRESHOLD:
        return True, f"observations:{len(bullets)}"
    return False, "under_threshold"


_COMPACTION_PROMPT_TEMPLATE = """\
Tarea: re-escribir la "curated summary" de una memoria persistente integrando
las observaciones nuevas. Esto NO es una respuesta a un usuario; es un
proceso interno de mantenimiento.

ENTRADA:

<curated_summary_actual>
{curated}
</curated_summary_actual>

<observations_log>
{observations}
</observations_log>

INSTRUCCIONES:

1. Producí UN bloque de markdown plano (sin headers, sin fenced code,
   sin preámbulo). Sale tal cual como reemplazo de la curated summary.
2. Integrá los hechos del observations log dentro del texto curado existente.
3. Si una observación nueva contradice un hecho previo, prevalece la nueva.
4. Si una observación es ambigua / opinión / efímera, descártala.
5. Mantenelo terso (máx ~{max_chars} caracteres). Preferí bullets cortos
   sobre prosa larga. Agrupá hechos relacionados.
6. Conservá especificidad: si una observación dice "Sam usa Folk CRM",
   NO la generalices a "Sam usa herramientas de CRM".
7. Si el curated_summary_actual es el placeholder "(no information yet
   about this …)", reemplazalo enteramente por los hechos del log.
8. Si el log está vacío y el curated ya está limpio, devolvé el curated
   tal cual.

OUTPUT: solo el nuevo cuerpo del curated summary, en español. Sin
explicación previa, sin "Aquí está el resumen:", sin código fenced.
"""


async def _call_compaction_model(curated: str, bullets: list[str]) -> str:
    """Single haiku call. Returns the new curated summary (trimmed,
    enforced under MAX_CURATED_CHARS). Raises on transport error so the
    caller can decide to skip + log."""
    settings = get_settings()
    model = settings.cheap_model
    prompt = _COMPACTION_PROMPT_TEMPLATE.format(
        curated=curated or "(vacío)",
        observations="\n".join(bullets) if bullets else "(vacío)",
        max_chars=MAX_CURATED_CHARS,
    )
    with _langfuse.start_as_current_observation(
        as_type="generation",
        name="memory:compaction",
        model=model,
        input=prompt,
    ) as gen:
        response = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
        )
        text = (response.choices[0].message.content or "").strip()
        usage = getattr(response, "usage", None)
        if usage is not None:
            gen.update(
                output=text,
                usage_details={
                    "input": getattr(usage, "prompt_tokens", 0) or 0,
                    "output": getattr(usage, "completion_tokens", 0) or 0,
                },
            )
        else:
            gen.update(output=text)

    # Strip accidental code fences if the model wrapped its output.
    text = re.sub(r"^```(?:\w+)?\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    text = text.strip()

    if len(text) > MAX_CURATED_CHARS:
        text = text[:MAX_CURATED_CHARS].rstrip() + "…"
    return text


async def compact_skill(workspace_id: uuid.UUID, skill_name: str) -> bool:
    """Compact one memory skill. Returns True iff the body was rewritten.

    Best-effort: returns False on any silent failure (skill missing, R2
    error, model error, optimistic-lock conflict). Logs the reason.
    """
    async with get_session() as session:
        skill = (
            await session.execute(
                select(Skill).where(
                    Skill.workspace_id == workspace_id,
                    Skill.name == skill_name,
                    Skill.source == "memory",
                )
            )
        ).scalar_one_or_none()
    if skill is None:
        log.info(
            "memory_compaction_skipped_missing",
            workspace_id=str(workspace_id),
            skill_name=skill_name,
        )
        return False

    seen_version = skill.version
    try:
        body = await skill_storage.download_skill_body(
            workspace_id=skill.workspace_id,
            skill_id=skill.id,
            version=skill.version,
            r2_ref=skill.body_r2_ref,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "memory_compaction_download_failed",
            workspace_id=str(workspace_id),
            skill_name=skill_name,
            error=str(exc)[:200],
        )
        return False

    eligible, reason = _is_eligible(body)
    if not eligible:
        return False

    curated, bullets, leading = _split_sections(body)
    if not bullets and curated:
        # Nothing new to integrate AND the curated section already exists.
        # The eligibility check passed because body size alone was over
        # threshold -- but that only happens if curated itself is huge,
        # which we can't compact without observations to integrate. Log
        # so an admin notices; this is a stuck state.
        log.warning(
            "memory_compaction_no_observations",
            workspace_id=str(workspace_id),
            skill_name=skill_name,
            body_size=len(body.encode("utf-8")),
        )
        return False

    try:
        new_curated = await _call_compaction_model(curated, bullets)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "memory_compaction_model_failed",
            workspace_id=str(workspace_id),
            skill_name=skill_name,
            error=str(exc)[:200],
        )
        return False

    if not new_curated:
        log.warning(
            "memory_compaction_empty_output",
            workspace_id=str(workspace_id),
            skill_name=skill_name,
        )
        return False

    new_body = _build_compacted_body(
        leading_metadata=leading, new_curated=new_curated
    )
    new_size = len(new_body.encode("utf-8"))

    # Optimistic locking: refetch + compare version. If an append landed
    # while we were calling haiku, skip this round.
    async with get_session() as session:
        current = await session.get(Skill, skill.id)
        if current is None or current.version != seen_version:
            log.info(
                "memory_compaction_version_conflict_skipped",
                workspace_id=str(workspace_id),
                skill_name=skill_name,
                seen_version=seen_version,
                current_version=getattr(current, "version", None),
            )
            return False

    try:
        await skill_registry.update_skill_body(
            skill_id=skill.id,
            new_body=new_body,
            new_size_bytes=new_size,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "memory_compaction_write_failed",
            workspace_id=str(workspace_id),
            skill_name=skill_name,
            error=str(exc)[:200],
        )
        return False

    log.info(
        "memory_compacted",
        workspace_id=str(workspace_id),
        skill_name=skill_name,
        reason=reason,
        observations_consumed=len(bullets),
        old_size=len(body.encode("utf-8")),
        new_size=new_size,
    )
    return True


async def compact_workspace(workspace_id: uuid.UUID) -> dict[str, int]:
    """Walk every memory skill in this workspace, compact eligible ones.
    Returns a small summary dict for logging."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Skill.name).where(
                    Skill.workspace_id == workspace_id,
                    Skill.source == "memory",
                )
            )
        ).scalars().all()

    compacted = 0
    examined = 0
    for name in rows:
        examined += 1
        try:
            if await compact_skill(workspace_id, name):
                compacted += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "memory_compaction_skill_unexpected_error",
                workspace_id=str(workspace_id),
                skill_name=name,
                error=str(exc)[:200],
            )
        # Small yield so we don't monopolize the event loop.
        await asyncio.sleep(0)
    return {"examined": examined, "compacted": compacted}


async def run_compaction_once() -> dict[str, int]:
    """One full sweep across all installed workspaces. Used by the
    background loop AND by an ad-hoc trigger (manual debug)."""
    async with get_session() as session:
        ws_ids = (
            await session.execute(
                select(Workspace.id).where(Workspace.bot_token.isnot(None))
            )
        ).scalars().all()

    total = {"workspaces": 0, "examined": 0, "compacted": 0}
    for wid in ws_ids:
        try:
            counts = await compact_workspace(wid)
            total["workspaces"] += 1
            total["examined"] += counts["examined"]
            total["compacted"] += counts["compacted"]
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "memory_compaction_workspace_unexpected_error",
                workspace_id=str(wid),
                error=str(exc)[:200],
            )
    return total


async def run_compaction_loop(interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> None:
    """Background lifespan task. Sleeps `interval_seconds` between sweeps.
    Cancellable from the lifespan shutdown branch."""
    log.info("memory_compaction_loop_started", interval_seconds=interval_seconds)
    try:
        while True:
            try:
                total = await run_compaction_once()
                if total["compacted"]:
                    log.info("memory_compaction_sweep_done", **total)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("memory_compaction_sweep_failed", error=str(exc)[:200])
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        log.info("memory_compaction_loop_cancelled")
        raise


__all__ = [
    "compact_skill",
    "compact_workspace",
    "run_compaction_once",
    "run_compaction_loop",
    "BODY_SIZE_THRESHOLD",
    "OBSERVATIONS_THRESHOLD",
    "MAX_CURATED_CHARS",
]
