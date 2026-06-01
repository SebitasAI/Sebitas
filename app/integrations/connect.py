"""In-conversation integration connect flow + auto-resume.

When the agent needs an app the workspace hasn't connected, request_integration
pauses the run (interrupt/checkpoint, slice-2 mechanism). This module decides
which provider serves the app (Composio preferred where available, Pipedream
fallback), posts the connect link in Slack, and resumes the paused run when
the connection completes -- via the incoming webhook OR a polling fallback.
Idempotent and tenant-scoped (external_user_id = workspace_id).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.db.models import IntegrationConnection
from app.db.session import get_session
from app.integrations import composio as cz
from app.integrations import gateway
from app.integrations.composio_provider import get_composio_provider
from app.integrations.pipedream_provider import get_provider as _get_pipedream_provider
from app.integrations.provider import IntegrationError
from app.integrations.routing import decide_provider_for_new_connection

log = structlog.get_logger(__name__)
_poll_tasks: set[asyncio.Task] = set()

# Pending integration_connection rows are zombies after this window.
# The poll task gives up after `connect_poll_timeout` (default 180s); we set
# this generously (10 min) to cover slow OAuth tabs the user may still be
# completing, while still cleaning up rows abandoned days/weeks ago.
# Anything older than this on a fresh connect attempt gets deleted so the
# routing layer re-decides the provider (Composio preferred for catalogued
# apps; before this guard, a stale 'pending' row with provider='pipedream'
# would silently pin the app to Pipedream forever).
_PENDING_ZOMBIE_TTL_SECONDS = 600


def _row_is_zombie_pending(row: IntegrationConnection) -> bool:
    """True when a `pending` row is older than the TTL and should be deleted
    before re-deciding the provider on a fresh connect attempt."""
    if row.status != "pending" or row.created_at is None:
        return False
    created = row.created_at
    if created.tzinfo is None:
        # Legacy rows persisted as naive UTC.
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds() > _PENDING_ZOMBIE_TTL_SECONDS


def _pipedream_webhook_uri() -> str | None:
    base = get_settings().public_base_url
    return f"{base.rstrip('/')}/integrations/pipedream/webhook" if base else None


def _composio_callback_uri() -> str | None:
    """Where Composio sends the user's browser after OAuth. Returning None
    makes Composio show its default 'success' page, which is fine for now;
    a branded landing page lives in a future slice. Pointing this at our
    webhook URL was the bug that caused 405 Method Not Allowed (the webhook
    is POST-only and Composio redirects with GET)."""
    return None


def _extract_composio_redirect_url(resp: dict) -> str | None:
    """Composio's /connected_accounts/link response shape varies across API
    versions. Try the documented keys at top level, then a few nested
    common patterns. Returns None if nothing matches (caller logs)."""
    if not isinstance(resp, dict):
        return None
    # Top-level candidates.
    for key in (
        "redirect_url", "redirectUrl", "connect_link_url",
        "url", "link", "auth_url", "authUrl",
    ):
        v = resp.get(key)
        if isinstance(v, str) and v.startswith(("http://", "https://")):
            return v
    # Nested candidates Composio sometimes uses.
    for outer in ("data", "connection", "link", "auth"):
        nested = resp.get(outer)
        if isinstance(nested, dict):
            for key in ("redirect_url", "redirectUrl", "url", "link"):
                v = nested.get(key)
                if isinstance(v, str) and v.startswith(("http://", "https://")):
                    return v
    return None


async def _mint_connect_link(provider_name: str, workspace_id: str, app: str) -> str | None:
    """Branch on provider to mint the right kind of connect link.

    Pipedream: a generic Connect link, the app slug is appended as a query param
    so their UI lands on the right connector. Composio: a per-toolkit OAuth
    redirect that already targets the right app server-side.
    """
    if provider_name == "composio":
        try:
            resp = await cz.initiate_connection(
                user_id=workspace_id,
                toolkit_slug=app,
                callback_url=_composio_callback_uri(),
            )
        except cz.ComposioHTTPError as e:
            log.warning(
                "composio_initiate_connection_failed",
                app=app, status=e.status, body=e.body[:300],
            )
            return None
        url = _extract_composio_redirect_url(resp)
        if url is None:
            # The call succeeded but we couldn't find the redirect URL in the
            # response. Log the response keys so we can update the extractor.
            log.warning(
                "composio_initiate_no_redirect_url",
                app=app,
                response_keys=list(resp.keys()) if isinstance(resp, dict) else None,
                response_sample=str(resp)[:300],
            )
        return url
    # Pipedream (default).
    try:
        token = await _get_pipedream_provider().create_connect_link(
            workspace_id, webhook_uri=_pipedream_webhook_uri(),
        )
    except IntegrationError as e:
        log.warning(
            "pipedream_create_connect_link_failed",
            app=app, kind=e.kind, status=e.status,
        )
        return None
    url = token.get("connect_link_url")
    if url:
        url += ("&" if "?" in url else "?") + f"app={app}"
    return url


async def _try_reconcile_existing(
    workspace_id: uuid.UUID, app: str, provider_name: str
) -> bool:
    """If `provider_name` already has an ACTIVE connection for (workspace, app),
    complete the pending flow without minting a new connect link.

    Covers the race where a previous attempt's OAuth succeeded on the provider
    side but our row never flipped to 'connected' (in-memory poll task died
    on a Render restart, or the webhook signature didn't match). Reading from
    the provider's source of truth on retry breaks the reconnect-loop.
    Returns True if we reconciled (caller should NOT mint a new link).
    """
    provider = (
        get_composio_provider() if provider_name == "composio"
        else _get_pipedream_provider()
    )
    try:
        accounts = await provider.list_accounts(str(workspace_id))
    except IntegrationError as e:
        log.warning(
            "reconcile_list_accounts_failed",
            app=app, provider=provider_name, kind=e.kind,
        )
        return False
    acc = provider.match_account_for_app(accounts, app)
    if not acc:
        return False
    acc_id = acc.get("id") or acc.get("account_id")
    if not acc_id:
        return False
    # Make sure it's actually usable (Composio statuses include INITIALIZING /
    # EXPIRED — we don't want to flip a row to 'connected' for those).
    try:
        problems = await provider.validate_connection(str(workspace_id), acc_id)
    except IntegrationError:
        return False
    if problems:
        return False
    log.info(
        "connect_reconciled_existing",
        app=app, provider=provider_name, account_id=acc_id,
        workspace_id=str(workspace_id),
    )
    await complete(str(workspace_id), app, acc_id)
    return True


async def start_connect(client, ctx: dict, app: str) -> None:
    """Decide provider, mint link, post Slack button, start polling fallback.
    The run is already paused (interrupt) at this point."""
    workspace_id = uuid.UUID(ctx["workspace_id"])
    if await gateway.is_connected(workspace_id, app):
        return  # already connected (the node also checks) -- nothing to do

    # If a row exists in 'pending' from a previous attempt, prefer its provider
    # so reconciliation looks at the same backend the user already authorized
    # against. For anything else (disconnected, missing account_id, or a stale
    # provider that doesn't match current routing preference) re-decide fresh
    # — preserving a stale 'pipedream' on a row that should now go through
    # Composio is exactly how the Simetrik metabase reset of 2026-05-29 ended
    # up routing to the wrong catalog and the bot hallucinated missing actions.
    #
    # Zombie cleanup: pending rows older than _PENDING_ZOMBIE_TTL_SECONDS
    # (10 min) are deleted up front so `decide_provider_for_new_connection`
    # gets to run fresh. The default `connect_poll_timeout` is 180s, so a
    # row that hasn't transitioned to 'connected' / 'disconnected' inside
    # 10 minutes is abandoned by definition (user closed the tab, the OAuth
    # link expired, the webhook never came back). Without this, a single
    # abandoned attempt pins (workspace, app) to its initial provider
    # forever -- exactly the salesforce → pipedream zombie Sam hit on
    # 2026-06-02.
    async with get_session() as session:
        prior = (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.app == app,
                )
            )
        ).scalar_one_or_none()
        if prior is not None and _row_is_zombie_pending(prior):
            log.info(
                "connect_zombie_pending_purged",
                workspace_id=str(workspace_id),
                app=app,
                prior_provider=prior.provider,
                age_seconds=int(
                    (
                        datetime.now(timezone.utc)
                        - (
                            prior.created_at.replace(tzinfo=timezone.utc)
                            if prior.created_at.tzinfo is None
                            else prior.created_at
                        )
                    ).total_seconds()
                ),
            )
            await session.delete(prior)
            await session.commit()
            prior = None
    # Only preserve the prior provider if the row is mid-flow (pending) AND
    # the provider field is set. Everything else gets a fresh decision so
    # we never end up routed against a provider the user has since moved
    # away from.
    if prior is not None and prior.status == "pending" and prior.provider:
        provider_name = prior.provider
    else:
        provider_name = await decide_provider_for_new_connection(app)
    log.info(
        "connect_provider_selected", app=app, provider=provider_name,
        workspace_id=str(workspace_id),
        prior_provider=(prior.provider if prior else None),
    )

    # Reconciliation: if a previous attempt left a row in 'pending' while the
    # provider already has an ACTIVE connection (poll task died on a deploy,
    # webhook signature mismatched), short-circuit here and mark the row
    # connected instead of minting a new link.
    #
    # Critical: only do this when status is 'pending'. If status is
    # 'disconnected' (i.e. disconnect_integration just ran a beat ago because
    # the user is going through a reinstall flow), reconciliation would
    # silently revive a stale ACTIVE connection from Composio's pile-up and
    # the agent would have no link to post. From the user's side it looks
    # like a no-op loop: "I asked to reconnect and nothing happened." For
    # disconnected rows or no row at all, always mint fresh.
    if prior is not None and prior.status == "pending":
        if await _try_reconcile_existing(workspace_id, app, provider_name):
            # Tell the user something happened so they're not staring at
            # silence wondering if the bot died.
            try:
                await client.chat_postMessage(
                    channel=ctx["channel"],
                    thread_ts=ctx.get("reply_thread_ts"),
                    text=(
                        f":white_check_mark: *{app}* ya estaba conectado del lado "
                        f"del proveedor. Reconcilié el estado y sigo solo."
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "reconcile_message_post_failed", app=app, error=str(exc)[:200],
                )
            return

    url = await _mint_connect_link(provider_name, ctx["workspace_id"], app)
    if url is None:
        await client.chat_postMessage(
            channel=ctx["channel"], thread_ts=ctx.get("reply_thread_ts"),
            text=f"No pude generar el link para conectar *{app}*. Reintentá en un momento.",
        )
        return

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

    # Persist after we know the message ts, with the chosen provider so the
    # poller + action calls all route through the same backend.
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
        row.provider = provider_name
        row.status = "pending"
        row.pending_run_id = ctx["run_id"]
        row.pending_ctx = new_ctx
        await session.commit()

    task = asyncio.create_task(_poll(ctx["workspace_id"], app, provider_name))
    _poll_tasks.add(task)
    task.add_done_callback(_poll_tasks.discard)


async def _poll(external_user_id: str, app: str, provider_name: str) -> None:
    """Fallback: poll the chosen provider until the account appears, then
    resume. Each provider's account-listing shape is different but
    `match_account_for_app` normalises that."""
    s = get_settings()
    provider = (
        get_composio_provider() if provider_name == "composio"
        else _get_pipedream_provider()
    )
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
    log.info("connect_poll_timeout", app=app, provider=provider_name)


async def resume_pending_polls() -> int:
    """On process startup, find every IntegrationConnection still in 'pending'
    with a `pending_run_id` (i.e. a paused agent run waiting for the connect to
    finish) and restart its poll task. Without this, a Render redeploy in the
    middle of a user's OAuth dance leaves the row stuck forever: the user
    completes OAuth on the provider's side, our row never flips, and the next
    user message triggers a reinstall loop.

    Idempotent: calling complete() on an already-completed row no-ops via the
    `pending_run_id is None` early return. Safe to run alongside any other
    in-flight poll tasks from the current process.

    Returns the count of polls restarted (used by callers / tests)."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.status == "pending",
                    IntegrationConnection.pending_run_id.is_not(None),
                )
            )
        ).scalars().all()
    count = 0
    for row in rows:
        provider_name = row.provider or "pipedream"
        external_user_id = str(row.workspace_id)
        task = asyncio.create_task(_poll(external_user_id, row.app, provider_name))
        _poll_tasks.add(task)
        task.add_done_callback(_poll_tasks.discard)
        count += 1
    if count:
        log.info("connect_polls_resumed", count=count)
    return count


async def periodic_resume_loop(interval_seconds: int = 300) -> None:
    """Background sweep: every `interval_seconds`, find pending rows whose poll
    task isn't tracked in this process and restart it. Catches rows that were
    born on a now-dead process (multi-instance deploys, transient crashes
    between restarts). The first poll task to find the account wins; the rest
    no-op via complete()'s idempotency. Cheap query: indexed on status."""
    while True:
        try:
            await resume_pending_polls()
        except Exception as exc:  # noqa: BLE001
            log.warning("periodic_resume_failed", error=str(exc))
        await asyncio.sleep(interval_seconds)


async def complete(external_user_id: str, app: str, account_id: str | None) -> None:
    """Mark connected + deactivate the Connect button(s) we posted + resume the
    paused run. Idempotent: the first of webhook/poll clears the pending run_id;
    the other becomes a no-op. Works for both providers because the row already
    carries which provider authorised; we don't need to re-decide here."""
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
    log.info(
        "integration_connected", app=app, workspace_id=external_user_id,
        provider=(row.provider if row else None),
    )

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
