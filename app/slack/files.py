"""Slack file ingest: download (bot-token auth) -> R2 (per-workspace prefix) ->
Anthropic content blocks (image/document via R2 presigned URL, text embedded
inline). Unsupported formats are reported gracefully and skipped."""

from __future__ import annotations

import aiohttp
import structlog

from app.artifacts import r2
from app.config import get_settings

log = structlog.get_logger(__name__)

_IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}
_PDF_MIMES = {"application/pdf"}
# Audio: Slack voicenotes are audio/mp4 (m4a). Common uploaded clips cover the rest.
# Video formats are intentionally out of scope (separate slice; needs audio extraction).
_AUDIO_MIMES = {
    "audio/mp4", "audio/x-m4a", "audio/m4a",
    "audio/mpeg", "audio/mp3",
    "audio/wav", "audio/x-wav", "audio/wave",
    "audio/ogg", "audio/opus",
    "audio/webm",
}
# Video: extract audio with ffmpeg -> transcribe. Visual frame extraction is
# explicitly out of scope (separate slice).
_VIDEO_MIMES = {
    "video/mp4",
    "video/quicktime",        # .mov
    "video/webm",
    "video/x-matroska",       # .mkv
}
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_EXACT = {
    "application/json", "application/xml", "application/yaml", "application/x-yaml",
}
_TEXT_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".sql", ".sh", ".yml", ".yaml", ".json",
    ".md", ".markdown", ".txt", ".csv", ".tsv", ".log", ".html", ".htm", ".xml",
    ".css", ".scss", ".rb", ".go", ".rs", ".java", ".c", ".cc", ".cpp", ".h", ".hpp",
    ".kt", ".kts", ".swift", ".php", ".tf", ".tfvars", ".toml", ".ini", ".conf", ".env",
}


def _ext(name: str) -> str:
    n = (name or "").lower()
    i = n.rfind(".")
    return n[i:] if i >= 0 else ""


def classify(mime: str, name: str) -> str:
    m = (mime or "").lower()
    if m in _IMAGE_MIMES:
        return "image"
    if m in _PDF_MIMES:
        return "pdf"
    if m in _AUDIO_MIMES:
        return "audio"
    if m in _VIDEO_MIMES:
        return "video"
    if any(m.startswith(p) for p in _TEXT_MIME_PREFIXES) or m in _TEXT_MIME_EXACT:
        return "text"
    if _ext(name) in _TEXT_EXTS:
        return "text"
    return "unsupported"


async def _download(url: str, bot_token: str, max_bytes: int) -> bytes:
    headers = {"Authorization": f"Bearer {bot_token}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, allow_redirects=True) as resp:
            resp.raise_for_status()
            data = await resp.read()
    if len(data) > max_bytes:
        raise ValueError(f"archivo supera el tope ({max_bytes} bytes)")
    return data


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n\n[... truncado: archivo de {len(text)} chars; se omitió el medio ...]\n\n{tail}"


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return data.decode("latin-1", errors="replace")


async def process_files(workspace_id: str, slack_files: list[dict], bot_token: str) -> dict:
    """Download Slack files, store in R2, build Anthropic content blocks.

    Returns a dict with: blocks (image/document), text_prepend (concatenated
    inline text files), attachments (records to persist), unsupported (human
    messages), supported_count, types (per-file kind, for tracing)."""
    settings = get_settings()
    blocks: list[dict] = []
    text_parts: list[str] = []
    records: list[dict] = []
    unsupported: list[str] = []
    types_seen: list[str] = []
    total = 0

    for f in slack_files or []:
        name = f.get("name") or f.get("title") or f.get("id", "file")
        mime = (f.get("mimetype") or "").lower()
        size = int(f.get("size") or 0)
        file_id = f.get("id") or ""
        url = f.get("url_private_download") or f.get("url_private")
        kind = classify(mime, name)
        types_seen.append(kind)

        if kind == "unsupported":
            unsupported.append(
                f"`{name}` — formato `{mime or _ext(name) or 'desconocido'}` aún no soportado. "
                "Probá exportarlo a PDF/CSV/imagen."
            )
            continue
        # Per-kind size caps. Video uses the bigger cap because after audio
        # extraction the bytes that actually leave us are ~10% of the original.
        size_cap = (
            settings.attachment_max_video_bytes if kind == "video"
            else settings.attachment_max_file_bytes
        )
        if size and size > size_cap:
            unsupported.append(
                f"`{name}` excede el tope por archivo ({size_cap // (1024 * 1024)} MB)."
            )
            continue
        if total + (size or 0) > settings.attachment_max_total_bytes:
            unsupported.append(f"Tope total por mensaje superado; me salté `{name}`.")
            continue
        if not url:
            unsupported.append(f"`{name}` sin URL descargable.")
            continue

        try:
            data = await _download(url, bot_token, size_cap)
        except Exception as exc:  # noqa: BLE001
            log.warning("file_download_failed", file_id=file_id, error=str(exc))
            unsupported.append(f"No pude bajar `{name}`: {exc}.")
            continue

        total += len(data)
        safe_name = (name or file_id).replace("/", "_").replace("\\", "_")
        r2_key = f"{workspace_id}/uploads/{file_id}/{safe_name}"
        try:
            await r2.put_bytes(r2_key, data, mime or "application/octet-stream")
        except Exception as exc:  # noqa: BLE001
            log.warning("r2_upload_failed", file_id=file_id, error=str(exc))
            unsupported.append(f"No pude almacenar `{name}`: {exc}.")
            continue

        records.append({
            "slack_file_id": file_id,
            "mime_type": mime or "application/octet-stream",
            "original_name": name,
            "size_bytes": len(data),
            "r2_ref": r2_key,
            "kind": kind,
        })

        if kind == "image":
            url_signed = await r2.presigned_get(r2_key)
            blocks.append({"type": "image", "source": {"type": "url", "url": url_signed}})
        elif kind == "pdf":
            url_signed = await r2.presigned_get(r2_key)
            blocks.append({
                "type": "document",
                "source": {"type": "url", "url": url_signed},
                "title": name,
            })
        elif kind == "text":
            text_parts.append(f"[Archivo: {name}]\n{_truncate(_decode(data), settings.attachment_max_text_chars)}")
        elif kind == "audio":
            # Transcribe once at ingest; persist the transcript on the row so
            # multi-turn re-attach reads it from DB without re-hitting Whisper.
            from app.slack.transcription import TranscriptionError, transcribe_audio_potentially_large
            try:
                transcript = await transcribe_audio_potentially_large(data, mime, name)
            except TranscriptionError as exc:
                log.warning("audio_transcription_failed", file_id=file_id, error=str(exc))
                unsupported.append(f"No pude transcribir `{name}`: {exc}")
                records.pop()
                continue
            records[-1]["transcript"] = transcript
            text_parts.append(f"[Audio transcript de `{name}`, {len(data)//1024} KB]:\n{transcript}")
        elif kind == "video":
            # Extract audio (ffmpeg local or E2B fallback), then transcribe
            # the same way as audio (chunked if necessary).
            from app.slack.ffmpeg_util import extract_audio, is_local_ffmpeg_available
            from app.slack.transcription import TranscriptionError, transcribe_audio_potentially_large
            try:
                audio_bytes = await extract_audio(data)
            except Exception as exc:  # noqa: BLE001
                log.warning("video_audio_extract_failed", file_id=file_id, error=str(exc))
                unsupported.append(
                    f"No pude extraer audio del video `{name}`: {exc}. "
                    + ("" if is_local_ffmpeg_available() else "Instalá ffmpeg local (brew install ffmpeg) o el fallback E2B falló.")
                )
                records.pop()
                continue
            try:
                transcript = await transcribe_audio_potentially_large(audio_bytes, "audio/mpeg", f"{name}.audio.mp3")
            except TranscriptionError as exc:
                log.warning("video_transcription_failed", file_id=file_id, error=str(exc))
                unsupported.append(f"No pude transcribir el video `{name}`: {exc}")
                records.pop()
                continue
            records[-1]["transcript"] = transcript
            records[-1]["attachment_metadata"] = {"extracted_audio_bytes": len(audio_bytes)}
            text_parts.append(f"[Video transcript de `{name}`, {len(data)//(1024*1024)} MB]:\n{transcript}")

    return {
        "blocks": blocks,
        "text_prepend": "\n\n".join(text_parts),
        "attachments": records,
        "unsupported": unsupported,
        "supported_count": len(records),
        "types": types_seen,
    }


async def build_attachment_blocks(attachment_rows, max_text_chars: int) -> tuple[list[dict], str]:
    """For multi-turn re-attach: from persisted MessageAttachment rows, build
    Anthropic content blocks (presigned URL) + a text-prepend string."""
    blocks: list[dict] = []
    text_parts: list[str] = []
    for a in attachment_rows:
        # YouTube and other non-file rows don't have a MIME-classifiable shape;
        # the row's `attachment_type` is the source of truth.
        if a.attachment_type == "youtube_link":
            if a.transcript:
                meta = a.attachment_metadata or {}
                header_bits = ["YouTube transcript"]
                if meta.get("title"):
                    header_bits.append(f"de «{meta['title']}»")
                if meta.get("channel"):
                    header_bits.append(f"por {meta['channel']}")
                if meta.get("duration_s"):
                    header_bits.append(f"~{int(meta['duration_s'])}s")
                text_parts.append(f"[{', '.join(header_bits)}]:\n{a.transcript}")
            continue
        kind = classify(a.mime_type, a.original_name or "")
        if kind == "image":
            url = await r2.presigned_get(a.r2_ref)
            blocks.append({"type": "image", "source": {"type": "url", "url": url}})
        elif kind == "pdf":
            url = await r2.presigned_get(a.r2_ref)
            blocks.append({
                "type": "document",
                "source": {"type": "url", "url": url},
                "title": a.original_name or "document.pdf",
            })
        elif kind == "text":
            try:
                data = await r2.get_bytes(a.r2_ref)
            except Exception:  # noqa: BLE001
                continue
            text_parts.append(f"[Archivo: {a.original_name or a.slack_file_id}]\n{_truncate(_decode(data), max_text_chars)}")
        elif kind == "audio":
            # Multi-turn rehydration: never re-transcribe; pull the cached
            # transcript from the row. If empty (rare: ingest failed), skip.
            if a.transcript:
                text_parts.append(
                    f"[Audio transcript de `{a.original_name or a.slack_file_id}`]:\n{a.transcript}"
                )
        elif kind == "video":
            # Same rehydration as audio: use the cached transcript of the
            # extracted track. Re-extracting + re-transcribing would burn
            # ffmpeg + Whisper on every turn.
            if a.transcript:
                text_parts.append(
                    f"[Video transcript de `{a.original_name or a.slack_file_id}`]:\n{a.transcript}"
                )
    return blocks, "\n\n".join(text_parts)
