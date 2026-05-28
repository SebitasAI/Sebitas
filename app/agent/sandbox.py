"""E2B code sandbox, one per agent run (keyed by run_id), reused across all
run_code calls in that run and destroyed when the run ends. Never shared across
runs or tenants. Files written to OUTPUT_DIR are uploaded to R2 under the
workspace prefix and returned as signed links.
"""

from __future__ import annotations

import structlog
from e2b_code_interpreter import AsyncSandbox

from app.agent.context import run_id_var, workspace_id_var
from app.artifacts import r2
from app.config import get_settings

log = structlog.get_logger(__name__)

OUTPUT_DIR = "/home/user/outputs"
_sandboxes: dict[str, AsyncSandbox] = {}

_CONTENT_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
    "svg": "image/svg+xml", "html": "text/html", "htm": "text/html", "csv": "text/csv",
    "json": "application/json", "txt": "text/plain", "pdf": "application/pdf",
}


def _content_type(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _CONTENT_TYPES.get(ext, "application/octet-stream")


async def _get_sandbox(run_id: str) -> AsyncSandbox:
    sbx = _sandboxes.get(run_id)
    if sbx is None:
        settings = get_settings()
        sbx = await AsyncSandbox.create(
            api_key=settings.e2b_api_key, timeout=settings.e2b_timeout_seconds
        )
        _sandboxes[run_id] = sbx
        log.info("sandbox_created", run_id=run_id)
    return sbx


async def close_run_sandbox(run_id: str) -> None:
    sbx = _sandboxes.pop(run_id, None)
    if sbx is not None:
        try:
            await sbx.kill()
            log.info("sandbox_closed", run_id=run_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("sandbox_close_failed", run_id=run_id, error=str(exc))


async def _collect_artifacts(sbx: AsyncSandbox, run_id: str, workspace_id: str) -> list[str]:
    try:
        entries = await sbx.files.list(OUTPUT_DIR)
    except Exception:
        return []
    links: list[str] = []
    for entry in entries:
        path = getattr(entry, "path", None) or f"{OUTPUT_DIR}/{getattr(entry, 'name', '')}"
        name = getattr(entry, "name", path.rsplit("/", 1)[-1])
        try:
            data = await sbx.files.read(path, format="bytes")
        except Exception:
            continue  # likely a directory
        key = f"{workspace_id}/{run_id}/{name}"
        try:
            url = await r2.upload_and_sign(key, data, _content_type(name))
            links.append(f"• {name}: {url}")
        except Exception as exc:  # noqa: BLE001
            log.warning("artifact_upload_failed", name=name, error=str(exc))
    return links


async def run_code(code: str) -> str:
    """Run Python in the current run's isolated sandbox; upload any files written
    to OUTPUT_DIR to R2 and return logs + signed artifact links."""
    run_id = run_id_var.get()
    workspace_id = workspace_id_var.get()
    if not run_id or not workspace_id:
        return "Error: no hay contexto de run/workspace para ejecutar código."

    sbx = await _get_sandbox(run_id)
    execution = await sbx.run_code(code)

    parts: list[str] = []
    if execution.error:
        parts.append(f"ERROR {execution.error.name}: {execution.error.value}")
    stdout = "".join(execution.logs.stdout) if execution.logs and execution.logs.stdout else ""
    stderr = "".join(execution.logs.stderr) if execution.logs and execution.logs.stderr else ""
    if stdout:
        parts.append(f"stdout:\n{stdout.strip()}")
    if stderr:
        parts.append(f"stderr:\n{stderr.strip()}")

    links = await _collect_artifacts(sbx, run_id, workspace_id)
    if links:
        parts.append("Artifacts (links firmados):\n" + "\n".join(links))

    log.info("run_code_done", run_id=run_id, artifacts=len(links), error=bool(execution.error))
    return "\n\n".join(parts) or "(ejecución sin salida)"
