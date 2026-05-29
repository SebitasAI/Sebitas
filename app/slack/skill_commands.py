"""Slack surface for the Skills feature: one slash command (`/misterr`) with
subcommands, plus the `file_shared` flow gated by a precursor state.

UI contract:
  /misterr skill upload  -> primes a 5-minute pending state for the user; the
                            next `.md` they upload in their DM is processed as
                            a skill (not as agent content).
  /misterr skill list    -> ephemeral list of installed skills with uninstall
                            buttons.
  /misterr skill remove <name>
                         -> uninstalls one skill for the user.
  /misterr skill info <name>
                         -> ephemeral block-kit with metadata + first 500
                            chars of the body.

Block-kit actions registered here:
  skill_install_confirm:<preview_id>  -> create skill + install
  skill_install_edit:<preview_id>     -> open edit modal
  skill_install_cancel:<preview_id>   -> discard preview
  skill_uninstall:<skill_id>          -> uninstall from list view
View submission:
  skill_install_modal                 -> re-render preview after user edits

Preview state (filename / body up to 256 KB / parsed fields) lives in the
`skill_preview` Postgres table, NOT in process memory. The previous
in-memory dict implementation lost previews on every Render redeploy and
produced 'La preview venció' errors. The DB-backed store is in
`app/skills/preview_store.py`; a background sweep in `app/main.py`
lifespan deletes expired rows.

The `_pending_uploads` precursor flag stays in-memory: it's a 5-minute
window per user before a `.md` upload arrives, and losing it on restart
just means the user retypes `/misterr skill upload`.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid

import aiohttp
import structlog
from slack_bolt.app.async_app import AsyncApp

from app.db.models import SkillPreview
from app.db.repository import upsert_app_user, upsert_workspace
from app.db.session import get_session
from app.skills import preview_store as _previews_store
from app.skills import registry as _registry
from app.skills import storage as _storage
from app.skills.frontmatter import (
    DESCRIPTION_MAX_LEN,
    NAME_MAX_LEN,
    _slugify,  # noqa: PLC2701 (internal but the right tool for the modal too)
    resolve_frontmatter,
)

log = structlog.get_logger(__name__)


# --------------------------------------------------------------------------- #
# Precursor flag (in-process, 5 min). Preview state itself lives in Postgres.
# --------------------------------------------------------------------------- #

_UPLOAD_PENDING_TTL_S = 5 * 60

# (team_id, slack_user_id) -> epoch expiry
_pending_uploads: dict[tuple[str, str], float] = {}


def _gc_pending() -> None:
    """Drop expired precursor flags. Called opportunistically on access; no
    background timer needed since the dict stays small (one entry per
    active user inside their 5-minute window)."""
    now = time.time()
    for key in list(_pending_uploads.keys()):
        if _pending_uploads[key] < now:
            del _pending_uploads[key]


def is_skill_upload_pending(team_id: str, slack_user_id: str) -> bool:
    """Public helper used by the generic message handler to know whether a
    `.md` file_share should be intercepted as a skill upload."""
    _gc_pending()
    return _pending_uploads.get((team_id, slack_user_id), 0) > time.time()


def _set_pending(team_id: str, slack_user_id: str) -> None:
    _pending_uploads[(team_id, slack_user_id)] = time.time() + _UPLOAD_PENDING_TTL_S


def _clear_pending(team_id: str, slack_user_id: str) -> None:
    _pending_uploads.pop((team_id, slack_user_id), None)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_MD_EXT_RE = re.compile(r"\.md$", re.IGNORECASE)
_MD_MIMES = {"text/markdown", "text/x-markdown", "text/plain"}


def _looks_like_markdown(f: dict) -> bool:
    name = f.get("name") or ""
    mime = f.get("mimetype") or ""
    filetype = f.get("filetype") or ""
    if filetype == "markdown":
        return True
    if mime in _MD_MIMES:
        return True
    return bool(_MD_EXT_RE.search(name))


async def _update_ephemeral(
    response_url: str,
    *,
    text: str,
    blocks: list[dict] | None = None,
) -> None:
    """Replace an ephemeral message via its `response_url` (provided in every
    block_actions interaction payload). Slack accepts up to 5 updates per URL
    in a 30-minute window; we use this to deactivate the action buttons after
    Install / Edit / Cancel / Uninstall so the user can't double-click.

    Best-effort: a failed update is logged but doesn't break the flow
    (the action itself already succeeded)."""
    if not response_url:
        return
    payload: dict = {"replace_original": True, "response_type": "ephemeral", "text": text}
    if blocks is not None:
        payload["blocks"] = blocks
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(response_url, json=payload) as resp:
                if resp.status >= 400:
                    body_text = await resp.text()
                    log.warning(
                        "ephemeral_update_failed",
                        status=resp.status, body=body_text[:200],
                    )
    except Exception as exc:  # noqa: BLE001
        log.warning("ephemeral_update_error", error=str(exc)[:200])


async def _download_md(url: str, bot_token: str) -> bytes:
    """Download a Slack file, enforcing the skill size cap before buffering
    the full body. Reads in chunks so a 5 MB malicious upload doesn't get
    fully loaded just to be rejected."""
    headers = {"Authorization": f"Bearer {bot_token}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, allow_redirects=True) as resp:
            resp.raise_for_status()
            buf = bytearray()
            async for chunk in resp.content.iter_chunked(8 * 1024):
                buf.extend(chunk)
                if len(buf) > _storage.MAX_BODY_BYTES:
                    raise ValueError(
                        f"archivo supera el tope ({_storage.MAX_BODY_BYTES} bytes)"
                    )
            return bytes(buf)


async def _resolve_user_uuid(team_id: str, slack_user_id: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Ensure (workspace, app_user) rows exist for this Slack identity. Used
    by every command so a brand-new user calling `/misterr` doesn't fail
    because their AppUser row hasn't been created yet (the generic message
    handler creates it on first message, but a user may run /misterr first)."""
    async with get_session() as session:
        workspace = await upsert_workspace(session, team_id)
        user = await upsert_app_user(session, workspace.id, slack_user_id)
        await session.commit()
        return workspace.id, user.id


# --------------------------------------------------------------------------- #
# Block-kit builders
# --------------------------------------------------------------------------- #

def _preview_blocks(preview_id: str, p: SkillPreview) -> list[dict]:
    inferred_note = (
        f"\n_Inferí los campos: {', '.join(p.inferred_fields)}._"
        if p.inferred_fields
        else ""
    )
    links_line = (
        f"\n• Referencias `[[..]]`: {', '.join(p.links)}" if p.links else ""
    )
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text":
            f":books: *Skill detectada en `{p.filename}`*{inferred_note}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text":
            f"• *name*: `{p.name}`\n"
            f"• *description*: {p.description}\n"
            f"• *activation*: `{p.activation}`"
            f"{links_line}"
        }},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Instalar"},
             "style": "primary", "action_id": f"skill_install_confirm:{preview_id}"},
            {"type": "button", "text": {"type": "plain_text", "text": "Editar"},
             "action_id": f"skill_install_edit:{preview_id}"},
            {"type": "button", "text": {"type": "plain_text", "text": "Cancelar"},
             "style": "danger", "action_id": f"skill_install_cancel:{preview_id}"},
        ]},
    ]


def _edit_modal_view(preview_id: str, p: SkillPreview) -> dict:
    return {
        "type": "modal",
        "callback_id": "skill_install_modal",
        "private_metadata": preview_id,
        "title": {"type": "plain_text", "text": "Editar skill"},
        "submit": {"type": "plain_text", "text": "Guardar"},
        "close": {"type": "plain_text", "text": "Cancelar"},
        "blocks": [
            {"type": "input", "block_id": "name",
             "label": {"type": "plain_text", "text": f"Nombre (slug, ≤ {NAME_MAX_LEN})"},
             "element": {"type": "plain_text_input", "action_id": "v",
                         "initial_value": p.name, "max_length": NAME_MAX_LEN}},
            {"type": "input", "block_id": "description",
             "label": {"type": "plain_text", "text": f"Descripción (≤ {DESCRIPTION_MAX_LEN})"},
             "element": {"type": "plain_text_input", "action_id": "v", "multiline": True,
                         "initial_value": p.description, "max_length": DESCRIPTION_MAX_LEN}},
            {"type": "input", "block_id": "activation",
             "label": {"type": "plain_text", "text": "Activación"},
             "element": {"type": "static_select", "action_id": "v",
                         "initial_option": {
                             "text": {"type": "plain_text", "text": p.activation},
                             "value": p.activation,
                         },
                         "options": [
                             {"text": {"type": "plain_text", "text": "always_active"},
                              "value": "always_active"},
                             {"text": {"type": "plain_text", "text": "on_demand"},
                              "value": "on_demand"},
                         ]}},
        ],
    }


def _list_blocks(installs) -> list[dict]:
    if not installs:
        return [{"type": "section", "text": {"type": "mrkdwn",
            "text": ":sparkles: No tenés skills instaladas. "
                    "Subí una con `/misterr skill upload`."}}]
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn",
        "text": f":books: *Tus skills* ({len(installs)})"}}]
    for swi in installs:
        when = swi.install.installed_at.strftime("%Y-%m-%d") if swi.install.installed_at else "?"
        blocks.append({"type": "section",
            "text": {"type": "mrkdwn",
                "text": f"• *{swi.skill.name}* [`{swi.effective_activation}`] — "
                        f"{swi.skill.description}\n_creada {when}_"},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": "Desinstalar"},
                "style": "danger",
                "action_id": f"skill_uninstall:{swi.skill.id}",
            },
        })
    return blocks


def _status_block(text: str) -> list[dict]:
    """Single-section block used to replace an action ephemeral after the
    user clicks a button. Carries no actions so it can't be re-triggered."""
    return [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]


def _name_collision_blocks(*, preview_id: str, skill_name: str) -> list[dict]:
    """Buttons shown when Install hits a workspace-level name collision.
    User picks: install the existing workspace skill, edit + rename to upload
    their own version, or cancel."""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text":
            f":warning: *Ya existe* `{skill_name}` *en este workspace* "
            "(la subió otro user, o quedó del catálogo). Elegí:"}},
        {"type": "section", "text": {"type": "mrkdwn", "text":
            "• *Instalar la existente*: te la agregamos a tu lista, sin "
            "tocar el contenido.\n"
            "• *Editar*: cambiás el nombre y subís tu versión propia.\n"
            "• *Cancelar*: descartamos este upload."}},
        {"type": "actions", "elements": [
            {"type": "button",
             "text": {"type": "plain_text", "text": "Instalar la existente"},
             "style": "primary",
             "action_id": f"skill_install_existing:{preview_id}"},
            {"type": "button",
             "text": {"type": "plain_text", "text": "Editar"},
             "action_id": f"skill_install_edit:{preview_id}"},
            {"type": "button",
             "text": {"type": "plain_text", "text": "Cancelar"},
             "style": "danger",
             "action_id": f"skill_install_cancel:{preview_id}"},
        ]},
    ]


def _info_blocks(swi) -> list[dict]:
    skill = swi.skill
    links_line = (
        f"\n• *referencias*: {', '.join(skill.links)}" if skill.links else ""
    )
    created_by = (
        f"\n• *creada por*: user `{skill.created_by_user_id}`"
        if skill.created_by_user_id
        else ""
    )
    return [
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f":books: *{skill.name}*\n_{skill.description}_"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": (
                f"• *activación efectiva*: `{swi.effective_activation}` "
                f"(default `{skill.activation_default}`)\n"
                f"• *tamaño*: {skill.size_bytes} bytes (v{skill.version})\n"
                f"• *creada*: {skill.created_at.strftime('%Y-%m-%d') if skill.created_at else '?'}"
                f"{created_by}{links_line}"
            )}},
    ]


# --------------------------------------------------------------------------- #
# Slash command + file_shared entry points
# --------------------------------------------------------------------------- #

async def handle_skill_file_upload(
    *,
    client,
    team_id: str,
    slack_user_id: str,
    channel: str,
    file_obj: dict,
    thread_ts: str | None,
) -> None:
    """Called from the generic message handler when a .md file_share arrives
    AND the precursor `/misterr skill upload` is pending. Downloads the file,
    runs frontmatter resolution, posts the preview block-kit."""
    # Consume the precursor now so a flood of files doesn't all enter this
    # path; a retry requires a fresh `/misterr skill upload`.
    _clear_pending(team_id, slack_user_id)

    size = file_obj.get("size") or 0
    if size and size > _storage.MAX_BODY_BYTES:
        await client.chat_postEphemeral(
            channel=channel, user=slack_user_id,
            thread_ts=thread_ts,
            text=f":warning: El archivo es de {size} bytes. El máximo para una skill "
                 f"es {_storage.MAX_BODY_BYTES} bytes. Subí una versión más chica.",
        )
        return

    url = file_obj.get("url_private_download") or file_obj.get("url_private")
    name = file_obj.get("name") or "skill.md"
    if not url:
        await client.chat_postEphemeral(
            channel=channel, user=slack_user_id,
            thread_ts=thread_ts,
            text=":warning: Slack no me dio una URL para descargar el archivo. Reintentá.",
        )
        return

    # We need the bot token. The slash + file_shared handlers receive `client`
    # already authorized for this workspace (Bolt resolves via authorize +
    # installation store), so `client.token` is the per-tenant token.
    bot_token = getattr(client, "token", None)
    if not bot_token:
        log.warning("skill_upload_no_bot_token", team_id=team_id)
        await client.chat_postEphemeral(
            channel=channel, user=slack_user_id, thread_ts=thread_ts,
            text=":warning: Problema interno con el token del bot, no pude descargar el archivo.",
        )
        return

    try:
        data = await _download_md(url, bot_token)
    except ValueError as exc:
        await client.chat_postEphemeral(
            channel=channel, user=slack_user_id, thread_ts=thread_ts,
            text=f":warning: {exc}",
        )
        return
    except Exception as exc:  # noqa: BLE001
        log.warning("skill_upload_download_failed", error=str(exc)[:200])
        await client.chat_postEphemeral(
            channel=channel, user=slack_user_id, thread_ts=thread_ts,
            text=":warning: No pude descargar el archivo. Reintentá.",
        )
        return

    raw_markdown = data.decode("utf-8", errors="replace")
    fm = await resolve_frontmatter(raw_markdown, filename=name)

    # Resolve workspace + app_user UUIDs once now, store on the preview row.
    # That way the Install / Edit / Cancel handlers don't need to round-trip
    # back through Slack team_id -> workspace_id on every click.
    workspace_id, app_user_id = await _resolve_user_uuid(team_id, slack_user_id)

    preview_id = await _previews_store.create_preview(
        workspace_id=workspace_id,
        app_user_id=app_user_id,
        slack_user_id=slack_user_id,
        channel_id=channel,
        filename=name,
        name=fm.name,
        description=fm.description,
        activation=fm.activation,
        body=fm.body,
        links=fm.links,
        inferred_fields=fm.inferred_fields,
    )
    preview = await _previews_store.get_preview(preview_id)
    if preview is None:
        # Shouldn't happen (we just inserted), but be defensive.
        await client.chat_postEphemeral(
            channel=channel, user=slack_user_id, thread_ts=thread_ts,
            text=":warning: No pude persistir la preview. Reintentá.",
        )
        return

    await client.chat_postEphemeral(
        channel=channel, user=slack_user_id, thread_ts=thread_ts,
        text="Skill detectada, revisá los campos antes de instalar.",
        blocks=_preview_blocks(str(preview_id), preview),
    )


# --------------------------------------------------------------------------- #
# Slash command dispatch
# --------------------------------------------------------------------------- #

_SUBCOMMAND_HELP = (
    "Uso: `/misterr skill <subcomando>`\n"
    "• `upload` — sube un archivo `.md` después de este comando.\n"
    "• `list` — lista tus skills instaladas.\n"
    "• `install <name>` — instala una skill que ya existe en el workspace "
    "(la subió otro user, o quedó del catálogo).\n"
    "• `info <name>` — detalles de una skill.\n"
    "• `remove <name>` — desinstalá una skill.\n"
)


async def _cmd_upload(*, client, team_id: str, slack_user_id: str, channel: str) -> None:
    _set_pending(team_id, slack_user_id)
    log.info("skill_upload_pending_set", team_id=team_id, user=slack_user_id)
    await client.chat_postEphemeral(
        channel=channel, user=slack_user_id,
        text=(":inbox_tray: Subí un archivo `.md` como adjunto en este chat. "
              "Lo proceso automáticamente. Tenés 5 minutos."),
    )


async def _cmd_list(*, client, team_id: str, slack_user_id: str, channel: str) -> None:
    _, user_id = await _resolve_user_uuid(team_id, slack_user_id)
    installs = await _registry.list_for_user(user_id)
    await client.chat_postEphemeral(
        channel=channel, user=slack_user_id,
        text=f"{len(installs)} skill(s) instalada(s).",
        blocks=_list_blocks(installs),
    )


async def _cmd_install(
    *, client, team_id: str, slack_user_id: str, channel: str, name: str
) -> None:
    """Install an EXISTING workspace skill for the caller. Used when a skill
    already lives in the workspace catalog (uploaded by another user, or left
    from a previous failed upload cycle) but the current user doesn't have it
    in their installs."""
    workspace_id, user_id = await _resolve_user_uuid(team_id, slack_user_id)
    skill = await _registry.get_skill_in_workspace(workspace_id, name)
    if skill is None:
        await client.chat_postEphemeral(
            channel=channel, user=slack_user_id,
            text=(f":warning: No hay ninguna skill llamada `{name}` en este "
                  f"workspace. Subí el `.md` con `/misterr skill upload`."),
        )
        return
    # Already-installed: friendly idempotent message instead of a silent reinstall.
    existing = await _registry.get_skill_for_user(user_id, name)
    if existing is not None:
        await client.chat_postEphemeral(
            channel=channel, user=slack_user_id,
            text=(f":information_source: `{name}` ya está instalada para vos "
                  f"(activación `{existing.effective_activation}`)."),
        )
        return
    await _registry.install_for_user(user_id=user_id, skill_id=skill.id)
    await client.chat_postEphemeral(
        channel=channel, user=slack_user_id,
        text=(f":white_check_mark: Instalada `{name}` para vos con activación "
              f"`{skill.activation_default}`. Hacé `/misterr skill list` para "
              "verla en tu lista."),
    )


async def _cmd_remove(
    *, client, team_id: str, slack_user_id: str, channel: str, name: str
) -> None:
    _, user_id = await _resolve_user_uuid(team_id, slack_user_id)
    swi = await _registry.get_skill_for_user(user_id, name)
    if swi is None:
        await client.chat_postEphemeral(
            channel=channel, user=slack_user_id,
            text=f":warning: No tenés instalada una skill llamada `{name}`.",
        )
        return
    await _registry.uninstall_for_user(user_id=user_id, skill_id=swi.skill.id)
    await client.chat_postEphemeral(
        channel=channel, user=slack_user_id,
        text=f":wastebasket: Desinstalé `{name}`. El archivo sigue disponible en el "
             "workspace por si querés reinstalarla.",
    )


async def _cmd_info(
    *, client, team_id: str, slack_user_id: str, channel: str, name: str
) -> None:
    _, user_id = await _resolve_user_uuid(team_id, slack_user_id)
    swi = await _registry.get_skill_for_user(user_id, name)
    if swi is None:
        await client.chat_postEphemeral(
            channel=channel, user=slack_user_id,
            text=f":warning: No tenés instalada una skill llamada `{name}`.",
        )
        return
    blocks = _info_blocks(swi)
    try:
        body = await _storage.download_skill_body(
            workspace_id=swi.skill.workspace_id,
            skill_id=swi.skill.id,
            version=swi.skill.version,
            r2_ref=swi.skill.body_r2_ref,
        )
        preview = body[:500] + ("…" if len(body) > 500 else "")
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": f"```\n{preview}\n```"}})
    except Exception as exc:  # noqa: BLE001
        log.warning("skill_info_body_failed", error=str(exc)[:200], skill_id=str(swi.skill.id))
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": "_(no pude cargar el preview del body)_"}})
    await client.chat_postEphemeral(
        channel=channel, user=slack_user_id, text=f"Skill `{name}`.", blocks=blocks,
    )


# --------------------------------------------------------------------------- #
# Persistence on install
# --------------------------------------------------------------------------- #

def _parse_preview_id(raw: str) -> uuid.UUID | None:
    """Action ids embed the preview UUID after a colon. Slack guarantees
    well-formed action ids, but be defensive: an invalid UUID returns None
    so the caller surfaces 'preview venció' instead of throwing."""
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError):
        return None


async def _persist_install(p: SkillPreview) -> str:
    """Create the skill row from a preview and install it for the preview's
    owner. workspace_id + app_user_id are already on the row (resolved at
    preview-creation time), so no extra Slack-id round-trip here."""
    size_bytes = len(p.body.encode("utf-8"))
    skill = await _registry.create_skill(
        workspace_id=p.workspace_id,
        name=p.name,
        description=p.description,
        activation_default=p.activation,  # type: ignore[arg-type]
        body=p.body,
        links=list(p.links or []),
        size_bytes=size_bytes,
        created_by_user_id=p.app_user_id,
    )
    await _registry.install_for_user(
        user_id=p.app_user_id,
        skill_id=skill.id,
        activation_override=None,  # default = skill.activation_default
    )
    return str(skill.id)


# --------------------------------------------------------------------------- #
# Registration with Bolt
# --------------------------------------------------------------------------- #

def register_skill_handlers(app: AsyncApp) -> None:
    @app.command("/misterr")
    async def on_misterr(ack, body, client):  # noqa: ANN001
        # Slack requires ACK < 3s; the actual work runs in a task.
        await ack()
        text = (body.get("text") or "").strip()
        team_id = body.get("team_id")
        slack_user_id = body.get("user_id")
        channel = body.get("channel_id")
        if not (team_id and slack_user_id and channel):
            return

        parts = text.split(maxsplit=2)
        # Tolerate both `/misterr skill upload` and `/misterr upload`. The
        # `skill` namespace is reserved for future subcommand groups.
        if parts and parts[0] == "skill":
            parts = parts[1:]
        if not parts:
            asyncio.create_task(client.chat_postEphemeral(
                channel=channel, user=slack_user_id, text=_SUBCOMMAND_HELP,
            ))
            return
        sub = parts[0]
        arg = parts[1] if len(parts) > 1 else ""

        async def _runner():
            try:
                if sub == "upload":
                    await _cmd_upload(client=client, team_id=team_id,
                                      slack_user_id=slack_user_id, channel=channel)
                elif sub == "list":
                    await _cmd_list(client=client, team_id=team_id,
                                    slack_user_id=slack_user_id, channel=channel)
                elif sub == "remove":
                    if not arg:
                        await client.chat_postEphemeral(
                            channel=channel, user=slack_user_id,
                            text="Faltó el nombre: `/misterr skill remove <name>`.",
                        )
                        return
                    await _cmd_remove(client=client, team_id=team_id,
                                      slack_user_id=slack_user_id,
                                      channel=channel, name=arg)
                elif sub == "install":
                    if not arg:
                        await client.chat_postEphemeral(
                            channel=channel, user=slack_user_id,
                            text="Faltó el nombre: `/misterr skill install <name>`.",
                        )
                        return
                    await _cmd_install(client=client, team_id=team_id,
                                       slack_user_id=slack_user_id,
                                       channel=channel, name=arg)
                elif sub == "info":
                    if not arg:
                        await client.chat_postEphemeral(
                            channel=channel, user=slack_user_id,
                            text="Faltó el nombre: `/misterr skill info <name>`.",
                        )
                        return
                    await _cmd_info(client=client, team_id=team_id,
                                    slack_user_id=slack_user_id,
                                    channel=channel, name=arg)
                else:
                    await client.chat_postEphemeral(
                        channel=channel, user=slack_user_id, text=_SUBCOMMAND_HELP,
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("skill_command_failed", sub=sub, error=str(exc)[:200])
                await client.chat_postEphemeral(
                    channel=channel, user=slack_user_id,
                    text=f":warning: Algo falló: {exc}",
                )

        asyncio.create_task(_runner())

    @app.action(re.compile(r"^skill_install_confirm:(.+)$"))
    async def on_install_confirm(ack, body, client):  # noqa: ANN001
        await ack()
        action_id = body["actions"][0]["action_id"]
        preview_id = _parse_preview_id(action_id.split(":", 1)[1])
        response_url = body.get("response_url", "")
        channel = body["channel"]["id"]
        slack_user_id = body["user"]["id"]
        p = await _previews_store.get_preview(preview_id) if preview_id else None
        if p is None:
            # The preview is gone (expired, deleted, or backend was rolled);
            # neutralise the buttons with a status line.
            await _update_ephemeral(
                response_url,
                text="La preview venció. Volvé a subir el archivo con `/misterr skill upload`.",
                blocks=_status_block(
                    ":warning: La preview venció. Volvé a subir el archivo con "
                    "`/misterr skill upload`."
                ),
            )
            return
        try:
            skill_id = await _persist_install(p)
        except _registry.SkillNameTaken:
            # Collision: the skill already exists in the workspace catalogue
            # but the user doesn't have it installed. Swap the buttons in
            # place so they can install the existing one with one click
            # instead of typing a command.
            await _update_ephemeral(
                response_url,
                text=(
                    f"Ya existe `{p.name}` en este workspace. Elegí qué hacer."
                ),
                blocks=_name_collision_blocks(
                    preview_id=str(preview_id),
                    skill_name=p.name,
                ),
            )
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("skill_install_failed", error=str(exc)[:200])
            await client.chat_postEphemeral(
                channel=channel, user=slack_user_id,
                text=f":warning: No pude guardar la skill: {exc}",
            )
            return
        await _previews_store.delete_preview(preview_id)
        await _update_ephemeral(
            response_url,
            text=f"Instalada `{p.name}` con activación `{p.activation}`.",
            blocks=_status_block(
                f":white_check_mark: *Instalada* `{p.name}` con activación "
                f"`{p.activation}`.\n• id: `{skill_id}`"
            ),
        )

    @app.action(re.compile(r"^skill_install_existing:(.+)$"))
    async def on_install_existing(ack, body, client):  # noqa: ANN001
        """One-click installer for an existing workspace skill whose name
        collided with an upload. Looks up the workspace skill, installs it
        for the preview's owner, and drops the preview row."""
        await ack()
        action_id = body["actions"][0]["action_id"]
        preview_id = _parse_preview_id(action_id.split(":", 1)[1])
        response_url = body.get("response_url", "")
        p = await _previews_store.get_preview(preview_id) if preview_id else None
        if p is None:
            await _update_ephemeral(
                response_url,
                text="La preview venció. Volvé a subir el archivo con `/misterr skill upload`.",
                blocks=_status_block(
                    ":warning: La preview venció. Volvé a subir el archivo con "
                    "`/misterr skill upload`."
                ),
            )
            return
        skill = await _registry.get_skill_in_workspace(p.workspace_id, p.name)
        if skill is None:
            # Edge case: the workspace skill disappeared between collision
            # and click (admin uninstalled? race?). Surface honestly.
            await _update_ephemeral(
                response_url,
                text=f"La skill `{p.name}` ya no existe en este workspace.",
                blocks=_status_block(
                    f":warning: La skill `{p.name}` ya no existe en este "
                    "workspace. Reintentá subiendo el archivo."
                ),
            )
            return
        # Idempotency: if somehow the user already has it (clicked twice,
        # someone else installed it for them), treat as a friendly no-op.
        existing = await _registry.get_skill_for_user(p.app_user_id, p.name)
        if existing is None:
            await _registry.install_for_user(
                user_id=p.app_user_id, skill_id=skill.id,
            )
        await _previews_store.delete_preview(preview_id)
        await _update_ephemeral(
            response_url,
            text=f"Instalada `{p.name}`.",
            blocks=_status_block(
                f":white_check_mark: *Instalada* `{p.name}` (versión "
                "existente del workspace). Hacé `/misterr skill list` "
                "para verla en tu lista."
            ),
        )

    @app.action(re.compile(r"^skill_install_edit:(.+)$"))
    async def on_install_edit(ack, body, client):  # noqa: ANN001
        await ack()
        action_id = body["actions"][0]["action_id"]
        preview_id_raw = action_id.split(":", 1)[1]
        preview_id = _parse_preview_id(preview_id_raw)
        response_url = body.get("response_url", "")
        p = await _previews_store.get_preview(preview_id) if preview_id else None
        if p is None:
            await _update_ephemeral(
                response_url,
                text="La preview venció. Volvé a subir el archivo con `/misterr skill upload`.",
                blocks=_status_block(
                    ":warning: La preview venció. Volvé a subir el archivo con "
                    "`/misterr skill upload`."
                ),
            )
            return
        # Deactivate the original buttons while the modal is open. After the
        # user submits, a fresh preview ephemeral is posted with new buttons.
        await _update_ephemeral(
            response_url,
            text=f"Editando `{p.name}`.",
            blocks=_status_block(
                f":pencil2: Editando `{p.name}`, esperá la nueva preview..."
            ),
        )
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=_edit_modal_view(preview_id_raw, p),
        )

    @app.action(re.compile(r"^skill_install_cancel:(.+)$"))
    async def on_install_cancel(ack, body, client):  # noqa: ANN001
        await ack()
        action_id = body["actions"][0]["action_id"]
        preview_id = _parse_preview_id(action_id.split(":", 1)[1])
        response_url = body.get("response_url", "")
        p = await _previews_store.get_preview(preview_id) if preview_id else None
        skill_name = p.name if p else "(desconocida)"
        if preview_id is not None:
            await _previews_store.delete_preview(preview_id)
        await _update_ephemeral(
            response_url,
            text=f"Cancelado: {skill_name}.",
            blocks=_status_block(f":x: *Cancelado.* No instalé `{skill_name}`."),
        )

    @app.view("skill_install_modal")
    async def on_install_modal_submit(ack, body, client, view):  # noqa: ANN001
        preview_id_raw = view.get("private_metadata") or ""
        preview_id = _parse_preview_id(preview_id_raw)
        p = await _previews_store.get_preview(preview_id) if preview_id else None
        if p is None:
            await ack(response_action="errors", errors={
                "name": "La preview venció. Subí el archivo de nuevo."
            })
            return
        vals = view["state"]["values"]
        new_name_raw = vals["name"]["v"]["value"] or ""
        new_desc = (vals["description"]["v"]["value"] or "").strip()[:DESCRIPTION_MAX_LEN]
        new_activation = vals["activation"]["v"]["selected_option"]["value"]
        new_name = _slugify(new_name_raw)
        if not new_name:
            await ack(response_action="errors", errors={"name": "Nombre inválido."})
            return
        if not new_desc:
            await ack(response_action="errors", errors={"description": "Descripción requerida."})
            return
        # Persist the edits + clear inferred-fields (an edit means user
        # intent, regardless of what the LLM guessed initially).
        updated = await _previews_store.update_preview(
            preview_id,
            name=new_name,
            description=new_desc,
            activation=new_activation,
            inferred_fields=[],
        )
        await ack()
        if updated is None:
            # Race: preview expired between get and update. Tell the user.
            await client.chat_postEphemeral(
                channel=p.channel_id, user=p.slack_user_id,
                text=":warning: La preview venció entre el edit y el save. Reintentá.",
            )
            return
        # Re-post the preview with updated fields. view_submission bodies
        # don't include channel.id, so we use the channel stashed on the
        # preview row at upload time.
        await client.chat_postEphemeral(
            channel=updated.channel_id,
            user=updated.slack_user_id,
            text="Preview actualizada.",
            blocks=_preview_blocks(preview_id_raw, updated),
        )

    @app.action(re.compile(r"^skill_uninstall:(.+)$"))
    async def on_uninstall(ack, body, client):  # noqa: ANN001
        await ack()
        action_id = body["actions"][0]["action_id"]
        skill_id_str = action_id.split(":", 1)[1]
        team_id = body.get("team", {}).get("id") or body.get("team_id")
        slack_user_id = body["user"]["id"]
        response_url = body.get("response_url", "")
        try:
            skill_uuid = uuid.UUID(skill_id_str)
        except (ValueError, TypeError):
            return
        _, user_id = await _resolve_user_uuid(team_id, slack_user_id)
        # Resolve the name before we uninstall so the status line can reference it.
        installs_before = await _registry.list_for_user(user_id)
        target_name = next(
            (s.skill.name for s in installs_before if s.skill.id == skill_uuid),
            None,
        )
        await _registry.uninstall_for_user(user_id=user_id, skill_id=skill_uuid)

        # Re-render the list view in place so the uninstalled skill disappears
        # and the remaining buttons stay live for further actions.
        installs_after = await _registry.list_for_user(user_id)
        if target_name:
            header_text = f":wastebasket: Desinstalé `{target_name}`."
        else:
            header_text = ":wastebasket: Skill desinstalada."
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": header_text}},
            *_list_blocks(installs_after),
        ]
        await _update_ephemeral(
            response_url,
            text=f"{len(installs_after)} skill(s) instalada(s).",
            blocks=blocks,
        )
