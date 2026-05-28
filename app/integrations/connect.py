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
from app.integrations import gateway
from app.integrations.pipedream_provider import get_provider
from app.integrations.provider import IntegrationError

log = structlog.get_logger(__name__)
_poll_tasks: set[asyncio.Task] = set()


def _webhook_uri() -> str | None:
    base = get_settings().public_base_url
    return f"{base.rstrip('/')}/integrations/pipedream/webhook" if base else None


async def start_connect(client, ctx: dict, app: str) -> None:
    """Create a pending connection + connect link, post a Slack button, and start
    the polling fallback. The run is already paused (interrupt) at this point."""
    workspace_id = uuid.UUID(ctx["workspace_id"])
    if await gateway.is_connected(workspace_id, app):
        return  # already connected (the node also checks) — nothing to do

    try:
        token = await get_provider().create_connect_link(
            ctx["workspace_id"], webhook_uri=_webhook_uri()
        )
    except IntegrationError as e:
        log.warning("create_connect_link_failed", app=app, kind=e.kind, status=e.status)
        await client.chat_postMessage(
            channel=ctx["channel"], thread_ts=ctx.get("reply_thread_ts"),
            text=f"No pude generar el link para conectar *{app}*. Reintentá en un momento.",
        )
        return
    url = token.get("connect_link_url")
    if url:
        url += ("&" if "?" in url else "?") + f"app={app}"

    # Carry forward any "Connect" buttons we've already posted for this app
    # (multiple requests inside the pending window each add their own button;
    # we deactivate ALL of them once the connection completes).
    async with get_session() as session:
        existing = (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.app == app,
                )
            )
        ).scalar_one_or_none()
        carried_buttons = list((existing.pending_ctx or {}).get("_buttons", [])) if existing and existing.pending_ctx else []

    blocks = [{"type": "section", "text": {"type": "mrkdwn",
        "text": f":electric_plug: Para continuar necesito acceso a *{app}*. Conectalo acá y sigo solo:"}}]
    if url:
        # Explicit action_id so Bolt has a known handler for the click event
        # (URL buttons fire a block_actions to Slack on click even though
        # the navigation is client-side; without a handler Bolt logs
        # "Unhandled request" for every connect button click).
        blocks.append({"type": "actions", "elements": [
            {"type": "button",
             "text": {"type": "plain_text", "text": f"Conectar {app}"},
             "style": "primary",
             "url": url,
             "action_id": "connect_url_button"}
        ]})
    resp = await client.chat_postMessage(
        channel=ctx["channel"], thread_ts=ctx.get("reply_thread_ts"),
        text=f"Para esto necesito acceso a {app}.", blocks=blocks,
    )
    post_ts = resp.get("ts") if isinstance(resp, dict) else (resp["ts"] if "ts" in resp else None)
    post_channel = (resp.get("channel") if isinstance(resp, dict) else resp["channel"]) or ctx["channel"]

    # Persist after we know the message ts, so `complete` can deactivate the
    # exact button(s) on success. Done in one tx with the rest of the upsert.
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
        buttons = list(carried_buttons)
        if post_ts:
            buttons.append({"channel": post_channel, "ts": post_ts})
        new_ctx = dict(ctx)
        new_ctx["_buttons"] = buttons
        row.status = "pending"
        row.pending_run_id = ctx["run_id"]
        row.pending_ctx = new_ctx
        await session.commit()

    task = asyncio.create_task(_poll(ctx["workspace_id"], app))
    _poll_tasks.add(task)
    task.add_done_callback(_poll_tasks.discard)


async def _poll(external_user_id: str, app: str) -> None:
    """Fallback: poll the provider until the account appears, then resume."""
    s = get_settings()
    provider = get_provider()
    waited = 0
    while waited < s.connect_poll_timeout:
        await asyncio.sleep(s.connect_poll_interval)
        waited += s.connect_poll_interval
        try:
            accounts = await provider.list_accounts(external_user_id)
        except IntegrationError:
            continue
        acc = provider.match_account_for_app(accounts, app)
        if acc:
            await complete(external_user_id, app, acc.get("id"))
            return
    log.info("connect_poll_timeout", app=app)


async def complete(external_user_id: str, app: str, account_id: str | None) -> None:
    """Mark connected + deactivate the Connect button(s) we posted + resume the
    paused run. Idempotent: the first of webhook/poll clears the pending run_id;
    the other becomes a no-op."""
    workspace_id = uuid.UUID(external_user_id)
    ctx = None
    buttons: list[dict] = []
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
        buttons = list((ctx or {}).get("_buttons", [])) if isinstance(ctx, dict) else []
        row.status = "connected"
        if account_id:
            row.pipedream_account_id = account_id
        row.pending_run_id = None
        row.pending_ctx = None
        await session.commit()
    log.info("integration_connected", app=app, workspace_id=external_user_id)

    # Deactivate every Connect-X button we posted for this app. Each was a
    # separate chat_postMessage (one per request_integration). On success we
    # chat_update them to a passive "connected" line, no buttons.
    if buttons:
        from slack_sdk.web.async_client import AsyncWebClient

        # Per-workspace token (multi-tenant install). We resolve from the
        # connection row's workspace_id; without a token we just skip the
        # button deactivation (cosmetic) -- not a hard failure.
        from app.slack.tokens import get_bot_token_by_workspace
        ws_pair = await get_bot_token_by_workspace(workspace_id)
        if not ws_pair:
            log.warning("connect_complete_no_token", workspace_id=external_user_id)
            slack = None
        else:
            slack = AsyncWebClient(token=ws_pair[0])
        if slack is None:
            buttons = []  # skip the loop below; just resume the run
        for b in buttons:
            ch, ts = b.get("channel"), b.get("ts")
            if not ch or not ts:
                continue
            try:
                await slack.chat_update(
                    channel=ch, ts=ts,
                    text=f"Conectado a {app}.",
                    blocks=[{"type": "section", "text": {"type": "mrkdwn",
                        "text": f":white_check_mark: *Conectado a {app}.* Sigo con tu pedido."}}],
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("connect_button_deactivate_failed", app=app, ts=ts, error=str(exc))

    if ctx:
        from app.agent.runner import resume_after_connect  # lazy: avoid import cycle
        # Strip UI bookkeeping from the resume ctx -- it's not part of the run.
        resume_ctx = {k: v for k, v in ctx.items() if not k.startswith("_")}
        await resume_after_connect(resume_ctx)
