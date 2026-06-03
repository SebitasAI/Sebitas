"""One-shot welcome DM after a new workspace install.

Sent the first time the installation store persists a workspace's bot
token. Idempotent across reinstalls and retries via a conditional
UPDATE on `workspace.welcome_dm_sent_at`.

Why this is its own module:
  - The message content is pure data; keeping it isolated makes it
    trivial to unit-test (no Slack client, no DB).
  - The send function is short enough to read in one screen, which is
    how we want install-path side effects to look (don't bury them in
    a 200-line installation handler).

Compliance:
  - Voice / tone: neutral LatAm tuteo, ~20% picante, warm.
  - Confidentiality: no internal subsystem names, no provider brand
    names, no model identifiers. The message describes what the bot
    does for the user, never how it works underneath.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import update

from app.db.models import Workspace
from app.db.session import get_session
from app.slack.crypto import TokenCryptoError, decrypt_token

log = structlog.get_logger(__name__)


# Short headline for clients that fall back to plaintext (notifications,
# screen readers). The full content lives in the blocks below.
WELCOME_FALLBACK_TEXT = "¡Hola! Soy Misterr, tu nuevo AI coworker."


WELCOME_BODY = (
    "👋 ¡Hola! Soy *Misterr*, tu nuevo AI coworker en Slack.\n"
    "\n"
    "Esto es lo básico para trabajar juntos:\n"
    "\n"
    "• *Menciona* `@Misterr` en cualquier canal para que te ayude con "
    "contexto del equipo.\n"
    "• *Escríbeme por DM* para conversaciones uno a uno.\n"
    "• *Súbeme a un canal* y voy a ver lo que pasa ahí para ayudarte mejor "
    "(uso el contexto, no te espío).\n"
    "• *Conecta tus herramientas*: pídeme _\"conecta Gmail\"_ o _\"conecta "
    "Salesforce\"_ y te paso el link para autorizar.\n"
    "• *Programa tareas recurrentes*: _\"todos los lunes a las 9, mándame "
    "un resumen del canal #ventas\"_.\n"
    "• *Enséñame cosas*: _\"recuerda que mi cliente top es X\"_ y lo tengo "
    "en cuenta en futuras conversaciones.\n"
    "• *Pídeme acciones*: enviar emails, crear tickets, resumir threads, "
    "hacer queries, lo que necesites.\n"
    "\n"
    "Para arrancar, pregúntame *\"¿qué puedes hacer?\"* o cuéntame en qué "
    "estás trabajando ahora mismo. 🚀"
)


def _build_blocks() -> list[dict]:
    """Slack blocks payload. Single section block keeps it readable; if
    we later want a header/divider/action buttons, add them here."""
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": WELCOME_BODY},
        }
    ]


async def maybe_send_welcome_dm(
    *,
    workspace_id: uuid.UUID,
    installer_slack_user_id: str,
) -> bool:
    """Send the welcome DM to the workspace's installer the FIRST time
    this is called for the workspace. Subsequent calls are no-ops.

    Returns True iff the DM was actually delivered. False covers all
    the "skip" paths: already sent, no installer id, no bot token
    available, Slack delivery error, missing workspace row, etc.

    Idempotency model: we reserve the slot with a conditional UPDATE
    (`SET welcome_dm_sent_at = NOW() WHERE id = ? AND welcome_dm_sent_at
    IS NULL`). If `rowcount == 0`, someone else (a concurrent retry or
    parallel install handler) already won the race; we exit without
    sending. If we win, we send; if the Slack call fails afterwards, we
    accept the missed welcome rather than risk double-sending later.
    Double-DM is a worse UX than no-DM.
    """
    if not installer_slack_user_id:
        return False

    # Atomic reservation. Captures bot_token for the send call. We
    # need the token AFTER the conditional update so a concurrent
    # winner doesn't pull stale credentials.
    async with get_session() as session:
        result = await session.execute(
            update(Workspace)
            .where(
                Workspace.id == workspace_id,
                Workspace.welcome_dm_sent_at.is_(None),
            )
            .values(welcome_dm_sent_at=datetime.now(timezone.utc).replace(tzinfo=None))
            .execution_options(synchronize_session=False)
            .returning(Workspace.bot_token)
        )
        row = result.first()
        await session.commit()

    if row is None:
        # Either we lost the race (already sent), or the workspace row
        # vanished between install_save and now. Either way, skip.
        log.info(
            "welcome_dm_already_sent_or_missing",
            workspace_id=str(workspace_id),
        )
        return False

    bot_token_enc = row[0]
    if not bot_token_enc:
        # Workspace exists, slot reserved, but no token to send with.
        # This means the install path called us before persisting the
        # token (caller bug). Log and bail.
        log.warning(
            "welcome_dm_no_bot_token",
            workspace_id=str(workspace_id),
        )
        return False

    try:
        token = decrypt_token(bot_token_enc)
    except TokenCryptoError as exc:
        log.warning(
            "welcome_dm_decrypt_failed",
            workspace_id=str(workspace_id), error=str(exc),
        )
        return False

    from slack_sdk.web.async_client import AsyncWebClient
    client = AsyncWebClient(token=token)
    try:
        # chat_postMessage with channel=<user_id> auto-opens the DM.
        await client.chat_postMessage(
            channel=installer_slack_user_id,
            text=WELCOME_FALLBACK_TEXT,
            blocks=_build_blocks(),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "welcome_dm_send_failed",
            workspace_id=str(workspace_id),
            installer_slack_user_id=installer_slack_user_id,
            error=str(exc)[:200],
        )
        # We've already marked the slot as "sent" to prevent retry
        # loops from spamming the user. The missed welcome is logged
        # and can be re-sent manually from the admin tools if needed.
        return False

    log.info(
        "welcome_dm_sent",
        workspace_id=str(workspace_id),
        installer_slack_user_id=installer_slack_user_id,
    )
    return True


__all__ = ["WELCOME_BODY", "WELCOME_FALLBACK_TEXT", "maybe_send_welcome_dm"]
