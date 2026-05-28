"""Audio transcription via Cloudflare Workers AI (Whisper).

Same Whisper model OpenAI exposes, hosted by Cloudflare. No new vendor (we're
already deep in CF for Pages + the dev tunnel). Cost is metered per second of
audio at parity with OpenAI.

Endpoint shape (current 2026):
    POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/openai/whisper
    Authorization: Bearer <CF_API_TOKEN>
    Content-Type: application/octet-stream
    Body: raw audio bytes

Response: {"success": bool, "result": {"text": "...", "words": [...], "vtt": "..."}, "errors": [...]}

Honest flags:
- The default `@cf/openai/whisper` model is medium-sized. Spanish quality is
  good for clear voicenotes. If clarity drops on noisy audio, the larger
  `@cf/openai/whisper-large-v3-turbo` variant is a one-line swap.
- 25MB hard cap (mirrors OpenAI's). Slack voicenotes are well under this.
- Audio bytes leave our process for Cloudflare's edge. Same privacy posture
  as any third-party transcription service.
"""

from __future__ import annotations

import aiohttp
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)

_MODEL = "@cf/openai/whisper"
_WHISPER_HARD_LIMIT = 25 * 1024 * 1024  # mirrors OpenAI's Whisper limit


class TranscriptionError(Exception):
    """Raised when transcription fails for an actionable reason (size, auth,
    upstream). The caller (process_files) maps it to a user-facing message."""


async def transcribe_audio(audio_bytes: bytes, mime: str, name: str) -> str:
    """Send `audio_bytes` to Workers AI Whisper and return the transcript text.

    Raises TranscriptionError with a short, user-facing reason on any failure.
    Caller is responsible for: persisting the result, surfacing errors to the
    user, and NOT calling this twice for the same r2_ref (cache externally)."""
    settings = get_settings()
    if not settings.cloudflare_api_token or not settings.cloudflare_account_id:
        raise TranscriptionError("Workers AI no configurado (faltan CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID).")
    if len(audio_bytes) > settings.attachment_max_audio_bytes:
        mb = settings.attachment_max_audio_bytes // (1024 * 1024)
        raise TranscriptionError(f"audio supera el tope de {mb} MB.")

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{settings.cloudflare_account_id}/ai/run/{_MODEL}"
    )
    headers = {
        "Authorization": f"Bearer {settings.cloudflare_api_token}",
        "Content-Type": "application/octet-stream",
    }
    timeout = aiohttp.ClientTimeout(total=120)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=audio_bytes, headers=headers) as resp:
                if resp.status == 401 or resp.status == 403:
                    raise TranscriptionError("Workers AI rechazó la autenticación (token con scope incorrecto?).")
                data = await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        log.warning("workers_ai_network_failed", error=str(exc))
        raise TranscriptionError(f"No pude alcanzar Workers AI: {exc}") from exc

    if not isinstance(data, dict) or not data.get("success"):
        errs = (data.get("errors") if isinstance(data, dict) else None) or [{"message": "unknown"}]
        msg = errs[0].get("message", "unknown") if isinstance(errs[0], dict) else "unknown"
        log.warning("workers_ai_returned_error", model=_MODEL, error=msg)
        raise TranscriptionError(f"Workers AI error: {msg}")

    text = ((data.get("result") or {}).get("text") or "").strip()
    if not text:
        raise TranscriptionError("Transcripción vacía (audio silencioso o no entendido).")

    # Metadata-only log (no full transcript -- could contain sensitive content).
    log.info(
        "audio_transcribed",
        mime=mime, bytes=len(audio_bytes), name=name, length_chars=len(text),
        preview=text[:50],
    )
    return text


async def transcribe_audio_potentially_large(audio_bytes: bytes, mime: str, name: str) -> str:
    """If `audio_bytes` fits the 25MB Whisper hard limit, transcribe directly.
    Otherwise, chunk via ffmpeg at ~10min boundaries and transcribe each chunk
    in parallel, returning concatenated text. Same error semantics as
    `transcribe_audio` -- a single chunk failure aborts the whole call."""
    import asyncio as _asyncio

    if len(audio_bytes) <= _WHISPER_HARD_LIMIT:
        return await transcribe_audio(audio_bytes, mime, name)

    from app.slack.ffmpeg_util import chunk_audio_for_whisper
    try:
        chunks = await chunk_audio_for_whisper(audio_bytes, _WHISPER_HARD_LIMIT)
    except Exception as exc:
        raise TranscriptionError(f"no pude chunkear el audio para transcribir: {exc}") from exc

    log.info("audio_chunked_for_whisper", name=name, total_bytes=len(audio_bytes), chunks=len(chunks))

    parts = await _asyncio.gather(
        *(transcribe_audio(chunk, mime, f"{name}#chunk{i}") for i, chunk in enumerate(chunks)),
        return_exceptions=False,
    )
    return "\n".join(p.strip() for p in parts if p)
