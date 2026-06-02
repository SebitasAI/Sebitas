"""E2B code sandbox, one per agent run (keyed by run_id), reused across all
run_code calls in that run and destroyed when the run ends. Never shared across
runs or tenants.

Artifacts (files written to OUTPUT_DIR) are handled in two layers:

1. **Slack upload** (user-facing). When the run has Slack thread context
   (channel + thread_ts in contextvars), every artifact is uploaded to
   Slack via files_upload_v2 directly into the user's thread. Slack
   hosts files indefinitely -- no expiration -- and the customer sees
   a native file preview right in the conversation. The tool result
   tells the LLM "I posted these files to Slack" so the LLM has no
   reason to hallucinate a download link.

2. **R2 backup** (system-facing). Same bytes are also uploaded to R2
   under the workspace prefix so future agent runs can re-read the
   artifact via signed URL (used for attachment re-hydration in
   `app/slack/files.py`). Signed URL lifetime bumped from 1h to 7d
   (see `Settings.artifact_url_expiry`) so threads spanning a few days
   don't break.

When Slack context is missing (non-Slack invocations: maybe a future
CLI / API path), we fall back to surfacing the R2 signed URL in the
tool result. That keeps the local-dev flow working without Slack.
"""

from __future__ import annotations

import uuid as _uuid

import structlog
from e2b_code_interpreter import AsyncSandbox
from slack_sdk.web.async_client import AsyncWebClient

from app.agent.context import (
    calling_channel_var,
    calling_reply_thread_ts_var,
    run_id_var,
    workspace_id_var,
)
from app.artifacts import r2
from app.config import get_settings
from app.slack.tokens import get_bot_token_by_workspace

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


async def _slack_client_for_workspace(workspace_id: str) -> AsyncWebClient | None:
    """Build an AsyncWebClient with the workspace's bot token, or None
    if we can't (no workspace context, no install, decryption failed).
    Used by `_collect_artifacts` to upload files directly to the
    user's Slack thread without going through R2 presigned URLs."""
    if not workspace_id:
        return None
    try:
        ws_uuid = _uuid.UUID(workspace_id)
    except (ValueError, TypeError):
        return None
    pair = await get_bot_token_by_workspace(ws_uuid)
    if pair is None:
        return None
    return AsyncWebClient(token=pair[0])


async def _collect_artifacts(sbx: AsyncSandbox, run_id: str, workspace_id: str) -> str:
    """Walk OUTPUT_DIR, upload each file to Slack (and R2 backup),
    return a single text block summarising what was posted.

    Returns "" if there are no artifacts -- caller treats that as
    "no artifact section to append" rather than an empty 'Artifacts:'
    header.

    Critical behavior: the returned text intentionally does NOT
    include R2 signed URLs when Slack upload succeeds. The LLM uses
    this text to write its final user-facing reply; if URLs were
    included, the LLM sometimes copies them ("aquí el link") AND the
    Slack file is already in the thread -- duplication + the URL is
    7-day expiring, worse than the Slack-hosted file. By telling the
    LLM "I posted these to Slack" without exposing a URL, the final
    reply naturally points at the Slack file ("subí el PDF arriba")
    instead of fabricating a download link."""
    try:
        entries = await sbx.files.list(OUTPUT_DIR)
    except Exception:
        return ""

    slack_channel = calling_channel_var.get() or ""
    slack_thread_ts = calling_reply_thread_ts_var.get() or ""
    slack_client = (
        await _slack_client_for_workspace(workspace_id)
        if slack_channel
        else None
    )

    posted_to_slack: list[str] = []
    r2_only_links: list[str] = []

    for entry in entries:
        path = getattr(entry, "path", None) or f"{OUTPUT_DIR}/{getattr(entry, 'name', '')}"
        name = getattr(entry, "name", path.rsplit("/", 1)[-1])
        try:
            data = await sbx.files.read(path, format="bytes")
        except Exception:
            # Usually a directory, sometimes a binary the SDK refuses
            # to deliver. Either way: skip and continue.
            continue

        # R2 upload (backup for re-hydration on later agent turns).
        # Best-effort: a failed R2 upload doesn't break Slack delivery.
        key = f"{workspace_id}/{run_id}/{name}"
        try:
            await r2.put_bytes(key, data, _content_type(name))
        except Exception as exc:  # noqa: BLE001
            log.warning("artifact_r2_upload_failed", name=name, error=str(exc))

        # Slack upload (user-facing, permanent).
        slack_ok = False
        if slack_client is not None:
            try:
                upload_kwargs: dict = {
                    "channel": slack_channel,
                    "content": data,
                    "filename": name,
                    "title": name,
                }
                if slack_thread_ts:
                    upload_kwargs["thread_ts"] = slack_thread_ts
                resp = await slack_client.files_upload_v2(**upload_kwargs)
                if isinstance(resp, dict):
                    slack_ok = bool(resp.get("ok"))
                else:
                    slack_ok = bool(getattr(resp, "data", {}).get("ok", True))
                if not slack_ok:
                    err = (
                        resp.get("error") if isinstance(resp, dict) else "unknown"
                    )
                    log.warning(
                        "slack_artifact_upload_rejected", name=name, error=err
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "slack_artifact_upload_failed", name=name, error=str(exc)
                )

        if slack_ok:
            posted_to_slack.append(name)
        else:
            # Fallback to R2 signed URL when Slack upload didn't land.
            # The LLM may include this URL in its reply -- acceptable
            # because there's no Slack file otherwise.
            try:
                url = await r2.presigned_get(key)
                r2_only_links.append(f"• {name}: {url}")
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "artifact_presign_failed", name=name, error=str(exc)
                )

    parts: list[str] = []
    if posted_to_slack:
        parts.append(
            "Subí los siguientes archivos al thread de Slack (permanentes, "
            "sin expiración, ya visibles para el usuario):\n"
            + "\n".join(f"• {n}" for n in posted_to_slack)
            + "\n\nNo construyas un link de descarga en tu respuesta -- el "
            "archivo ya está adjunto en el thread. Solo refierete a él por "
            "nombre."
        )
    if r2_only_links:
        parts.append(
            "Links firmados de descarga (válidos 7 días) -- el upload directo "
            "a Slack no funcionó para estos archivos:\n"
            + "\n".join(r2_only_links)
        )
    return "\n\n".join(parts)


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

    artifact_summary = await _collect_artifacts(sbx, run_id, workspace_id)
    if artifact_summary:
        parts.append(artifact_summary)

    log.info("run_code_done", run_id=run_id, artifacts=len(links), error=bool(execution.error))
    return "\n\n".join(parts) or "(ejecución sin salida)"
