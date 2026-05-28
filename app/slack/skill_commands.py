"""Slack surface for the Skills feature: one slash command (`/sebitas`) with
subcommands, plus the `file_shared` flow gated by a precursor state.

UI contract:
  /sebitas skill upload  -> primes a 5-minute pending state for the user; the
                            next `.md` they upload in their DM is processed as
                            a skill (not as agent content).
  /sebitas skill list    -> ephemeral list of installed skills with uninstall
                            buttons.
  /sebitas skill remove <name>
                         -> uninstalls one skill for the user.
  /sebitas skill info <name>
                         -> ephemeral block-kit with metadata + first 500
                            chars of the body.

Block-kit actions registered here:
  skill_install_confirm:<preview_id>  -> create skill + install
  skill_install_edit:<preview_id>     -> open edit modal
  skill_install_cancel:<preview_id>   -> discard preview
  skill_uninstall:<skill_id>          -> uninstall from list view
View submission:
  skill_install_modal                 -> re-render preview after user edits

State that doesn't fit in a block-kit `value` (raw body of up to 256 KB)
lives in the in-memory preview cache, keyed by `preview_id`. On a Render
restart the cache resets; the user just types `/sebitas skill upload` again.
That's acceptable for an interactive flow that lives for minutes, not hours.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field

import aiohttp
import structlog
from slack_bolt.app.async_app import AsyncApp

from app.db.repository import upsert_app_user, upsert_workspace
from app.db.session import get_session
from app.skills import registry as _registry
from app.skills import storage as _storage
from app.skills.frontmatter import (
    DESCRIPTION_MAX_LEN,
    NAME_MAX_LEN,
    Frontmatter,
    _slugify,  # noqa: PLC2701 (internal but the right tool for the modal too)
    resolve_frontmatter,
)

log = structlog.get_logger(__name__)


# --------------------------------------------------------------------------- #
# State stores (in-process; restart = reset).
# --------------------------------------------------------------------------- #

_UPLOAD_PENDING_TTL_S = 5 * 60
_PREVIEW_TTL_S = 30 * 60

# (team_id, slack_user_id) -> epoch expiry
_pending_uploads: dict[tuple[str, str], float] = {}


@dataclass
class PreviewState:
    """Cached parse result for an upload, waiting on user confirmation.

    The body is kept here (not in the button value) because Slack caps
    block-kit action values at 2 KB while our bodies can be 256 KB.

    `channel_id` is the conversation where we posted the original ephemeral.
    We stash it so the modal-submit handler can repost the updated preview to
    the same place (modal `view_submission` bodies don't carry channel)."""

    workspace_team_id: str
    slack_user_id: str
    channel_id: str
    filename: str
    name: str
    description: str
    activation: str
    body: str
    links: list[str]
    inferred_fields: list[str]
    expires_at: float = field(default_factory=lambda: time.time() + _PREVIEW_TTL_S)


_previews: dict[str, PreviewState] = {}


def _gc_state() -> None:
    """Drop expired entries. Called opportunistically on each access; no
    background timer needed since traffic is bursty."""
    now = time.time()
    for key in list(_pending_uploads.keys()):
        if _pending_uploads[key] < now:
            del _pending_uploads[key]
    for pid in list(_previews.keys()):
        if _previews[pid].expires_at < now:
            del _previews[pid]


def is_skill_upload_pending(team_id: str, slack_user_id: str) -> bool:
    """Public helper used by the generic message handler to know whether a
    `.md` file_share should be intercepted as a skill upload."""
    _gc_state()
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
    by every command so a brand-new user calling `/sebitas` doesn't fail
    because their AppUser row hasn't been created yet (the generic message
    handler creates it on first message, but a user may run /sebitas first)."""
    async with get_session() as session:
        workspace = await upsert_workspace(session, team_id)
        user = await upsert_app_user(session, workspace.id, slack_user_id)
        await session.commit()
        return workspace.id, user.id


# --------------------------------------------------------------------------- #
# Block-kit builders
# --------------------------------------------------------------------------- #

def _preview_blocks(preview_id: str, p: PreviewState) -> list[dict]:
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


def _edit_modal_view(preview_id: str, p: PreviewState) -> dict:
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
                    "Subí una con `/sebitas skill upload`."}}]
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
    AND the precursor `/sebitas skill upload` is pending. Downloads the file,
    runs frontmatter resolution, posts the preview block-kit."""
    # Consume the precursor now so a flood of files doesn't all enter this
    # path; a retry requires a fresh `/sebitas skill upload`.
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

    preview_id = uuid.uuid4().hex
    _previews[preview_id] = PreviewState(
        workspace_team_id=team_id,
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

    await client.chat_postEphemeral(
        channel=channel, user=slack_user_id, thread_ts=thread_ts,
        text="Skill detectada, revisá los campos antes de instalar.",
        blocks=_preview_blocks(preview_id, _previews[preview_id]),
    )


# --------------------------------------------------------------------------- #
# Slash command dispatch
# --------------------------------------------------------------------------- #

_SUBCOMMAND_HELP = (
    "Uso: `/sebitas skill <subcomando>`\n"
    "• `upload` — sube un archivo `.md` después de este comando.\n"
    "• `list` — lista tus skills instaladas.\n"
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

async def _persist_install(p: PreviewState) -> str:
    """Resolve user + create-or-reuse skill row + install for that user.
    Returns the new/existing skill id as a string for logging."""
    workspace_id, user_id = await _resolve_user_uuid(
        p.workspace_team_id, p.slack_user_id
    )
    size_bytes = len(p.body.encode("utf-8"))
    try:
        skill = await _registry.create_skill(
            workspace_id=workspace_id,
            name=p.name,
            description=p.description,
            activation_default=p.activation,  # type: ignore[arg-type]
            body=p.body,
            links=p.links,
            size_bytes=size_bytes,
            created_by_user_id=user_id,
        )
        skill_id = skill.id
    except _registry.SkillNameTaken:
        # User confirmed install but the name was taken between preview and
        # confirm (race, or they happened to upload a duplicate). Surface
        # cleanly via the caller.
        raise
    await _registry.install_for_user(
        user_id=user_id,
        skill_id=skill_id,
        activation_override=None,  # default = skill.activation_default
    )
    return str(skill_id)


# --------------------------------------------------------------------------- #
# Registration with Bolt
# --------------------------------------------------------------------------- #

def register_skill_handlers(app: AsyncApp) -> None:
    @app.command("/sebitas")
    async def on_sebitas(ack, body, client):  # noqa: ANN001
        # Slack requires ACK < 3s; the actual work runs in a task.
        await ack()
        text = (body.get("text") or "").strip()
        team_id = body.get("team_id")
        slack_user_id = body.get("user_id")
        channel = body.get("channel_id")
        if not (team_id and slack_user_id and channel):
            return

        parts = text.split(maxsplit=2)
        # Tolerate both `/sebitas skill upload` and `/sebitas upload`. The
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
                            text="Faltó el nombre: `/sebitas skill remove <name>`.",
                        )
                        return
                    await _cmd_remove(client=client, team_id=team_id,
                                      slack_user_id=slack_user_id,
                                      channel=channel, name=arg)
                elif sub == "info":
                    if not arg:
                        await client.chat_postEphemeral(
                            channel=channel, user=slack_user_id,
                            text="Faltó el nombre: `/sebitas skill info <name>`.",
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
        preview_id = action_id.split(":", 1)[1]
        _gc_state()
        p = _previews.get(preview_id)
        channel = body["channel"]["id"]
        slack_user_id = body["user"]["id"]
        if p is None:
            await client.chat_postEphemeral(
                channel=channel, user=slack_user_id,
                text=":warning: La preview venció. Volvé a subir el archivo con `/sebitas skill upload`.",
            )
            return
        try:
            skill_id = await _persist_install(p)
        except _registry.SkillNameTaken:
            await client.chat_postEphemeral(
                channel=channel, user=slack_user_id,
                text=(f":warning: Ya existe una skill con el nombre `{p.name}` en este "
                      "workspace. Hacé `Editar` para cambiarle el nombre."),
            )
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("skill_install_failed", error=str(exc)[:200])
            await client.chat_postEphemeral(
                channel=channel, user=slack_user_id,
                text=f":warning: No pude guardar la skill: {exc}",
            )
            return
        _previews.pop(preview_id, None)
        await client.chat_postEphemeral(
            channel=channel, user=slack_user_id,
            text=(f":white_check_mark: Listo. Instalé `{p.name}` con activación "
                  f"`{p.activation}`. id `{skill_id}`."),
        )

    @app.action(re.compile(r"^skill_install_edit:(.+)$"))
    async def on_install_edit(ack, body, client):  # noqa: ANN001
        await ack()
        action_id = body["actions"][0]["action_id"]
        preview_id = action_id.split(":", 1)[1]
        _gc_state()
        p = _previews.get(preview_id)
        if p is None:
            await client.chat_postEphemeral(
                channel=body["channel"]["id"], user=body["user"]["id"],
                text=":warning: La preview venció. Volvé a subir el archivo con `/sebitas skill upload`.",
            )
            return
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=_edit_modal_view(preview_id, p),
        )

    @app.action(re.compile(r"^skill_install_cancel:(.+)$"))
    async def on_install_cancel(ack, body, client):  # noqa: ANN001
        await ack()
        action_id = body["actions"][0]["action_id"]
        preview_id = action_id.split(":", 1)[1]
        _previews.pop(preview_id, None)
        await client.chat_postEphemeral(
            channel=body["channel"]["id"], user=body["user"]["id"],
            text="Cancelado. No instalé nada.",
        )

    @app.view("skill_install_modal")
    async def on_install_modal_submit(ack, body, client, view):  # noqa: ANN001
        preview_id = view.get("private_metadata") or ""
        p = _previews.get(preview_id)
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
        # Update the cached preview; the user still has to press Instalar on
        # the original ephemeral (which still has the same preview_id).
        p.name = new_name
        p.description = new_desc
        p.activation = new_activation
        # An edit overrides whatever we inferred; clear the inferred-fields
        # note so the preview reflects user intent.
        p.inferred_fields = []
        await ack()
        # Re-post the preview with updated fields. view_submission bodies
        # don't include channel.id, so we use the channel we stashed at
        # preview-creation time. chat_postEphemeral requires the user be
        # present in the channel, which holds for the original DM/channel
        # where the file was uploaded.
        await client.chat_postEphemeral(
            channel=p.channel_id,
            user=p.slack_user_id,
            text="Preview actualizada.",
            blocks=_preview_blocks(preview_id, p),
        )

    @app.action(re.compile(r"^skill_uninstall:(.+)$"))
    async def on_uninstall(ack, body, client):  # noqa: ANN001
        await ack()
        action_id = body["actions"][0]["action_id"]
        skill_id_str = action_id.split(":", 1)[1]
        team_id = body.get("team", {}).get("id") or body.get("team_id")
        slack_user_id = body["user"]["id"]
        channel = body["channel"]["id"]
        try:
            skill_uuid = uuid.UUID(skill_id_str)
        except (ValueError, TypeError):
            return
        _, user_id = await _resolve_user_uuid(team_id, slack_user_id)
        await _registry.uninstall_for_user(user_id=user_id, skill_id=skill_uuid)
        await client.chat_postEphemeral(
            channel=channel, user=slack_user_id,
            text=":wastebasket: Skill desinstalada.",
        )
