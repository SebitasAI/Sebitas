"""In-conversation integration connect flow + auto-resume.

When the agent needs an app the workspace hasn't connected, request_integration
pauses the run (interrupt/checkpoint, slice-2 mechanism). This module posts the
Pipedream connect link in Slack and resumes the paused run when the connection
completes — via the incoming webhook OR a polling fallback. Idempotent and
tenant-scoped (external_user_id = workspace_id).
"""

from __future__ import annotations

import asyncio
import uuid

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.db.models import IntegrationConnection
from app.db.session import get_session
from app.integrations import gateway, pipedream

log = structlog.get_logger(__name__)
_poll_tasks: set[asyncio.Task] = set()


def _webhook_uri() -> str | None:
    base = get_settings().public_base_url
    return f"{base.rstrip('/')}/integrations/pipedream/webhook" if base else None


def _match_account(accounts: list[dict], app: str) -> dict | None:
    for a in accounts:
        ao = a.get("app") or {}
        if (ao.get("name_slug") or ao.get("name")) == app:
            return a
    return None


async def start_connect(client, ctx: dict, app: str) -> None:
    """Create a pending connection + connect link, post a Slack button, and start
    the polling fallback. The run is already paused (interrupt) at this point."""
    workspace_id = uuid.UUID(ctx["workspace_id"])
    if await gateway.is_connected(workspace_id, app):
        return  # already connected (the node also checks) — nothing to do

    token = await pipedream.create_connect_token(ctx["workspace_id"], webhook_uri=_webhook_uri())
    url = token.get("connect_link_url")
    if url:
        url += ("&" if "?" in url else "?") + f"app={app}"

    async with get_session() as session:
        row = (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.app == app,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = IntegrationConnection(workspace_id=workspace_id, app=app)
            session.add(row)
        row.status = "pending"
        row.pending_run_id = ctx["run_id"]
        row.pending_ctx = ctx
        await session.commit()

    blocks = [{"type": "section", "text": {"type": "mrkdwn",
        "text": f":electric_plug: Para continuar necesito acceso a *{app}*. Conectalo acá y sigo solo:"}}]
    if url:
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": f"Conectar {app}"}, "style": "primary", "url": url}
        ]})
    await client.chat_postMessage(
        channel=ctx["channel"], thread_ts=ctx.get("reply_thread_ts"),
        text=f"Para esto necesito acceso a {app}.", blocks=blocks,
    )

    task = asyncio.create_task(_poll(ctx["workspace_id"], app))
    _poll_tasks.add(task)
    task.add_done_callback(_poll_tasks.discard)


async def _poll(external_user_id: str, app: str) -> None:
    """Fallback: poll Pipedream until the account appears, then resume."""
    s = get_settings()
    waited = 0
    while waited < s.connect_poll_timeout:
        await asyncio.sleep(s.connect_poll_interval)
        waited += s.connect_poll_interval
        try:
            accounts = await pipedream.list_accounts(external_user_id)
        except Exception:  # noqa: BLE001
            continue
        acc = _match_account(accounts, app)
        if acc:
            await complete(external_user_id, app, acc.get("id"))
            return
    log.info("connect_poll_timeout", app=app)


async def complete(external_user_id: str, app: str, account_id: str | None) -> None:
    """Mark connected + resume the paused run. Idempotent: the first of
    webhook/poll clears the pending run_id; the other becomes a no-op."""
    workspace_id = uuid.UUID(external_user_id)
    ctx = None
    async with get_session() as session:
        row = (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.app == app,
                )
            )
        ).scalar_one_or_none()
        if row is None or row.pending_run_id is None:
            return  # nothing pending -> already handled (dedupe)
        ctx = row.pending_ctx
        row.status = "connected"
        if account_id:
            row.pipedream_account_id = account_id
        row.pending_run_id = None
        row.pending_ctx = None
        await session.commit()
    log.info("integration_connected", app=app, workspace_id=external_user_id)
    if ctx:
        from app.agent.runner import resume_after_connect  # lazy: avoid import cycle
        await resume_after_connect(ctx)
