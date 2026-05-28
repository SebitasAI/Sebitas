"""ffmpeg helpers: extract audio from video + chunk audio for Whisper.

Two paths, transparent to callers:
- Local subprocess: if `ffmpeg` is on PATH. Sub-second startup; preferred.
- E2B one-shot sandbox: fallback when ffmpeg isn't installed. Sandbox image
  ships ffmpeg preinstalled. +3-5s for sandbox startup; documented.

Both return the same shape (bytes for extract; list[bytes] for chunk). Errors
surface as RuntimeError with a short reason; callers map to user messages.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile

import structlog

log = structlog.get_logger(__name__)

_HAS_LOCAL_FFMPEG = shutil.which("ffmpeg") is not None
_CHUNK_SECONDS = 600  # 10 minutes per chunk; well under Whisper 25MB even for high-bitrate.


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


async def extract_audio(video_bytes: bytes) -> bytes:
    """Returns mp3 bytes from `video_bytes`. Local ffmpeg if present, else E2B."""
    if _HAS_LOCAL_FFMPEG:
        return await _extract_audio_local(video_bytes)
    log.info("ffmpeg_using_e2b_fallback", op="extract")
    return await _extract_audio_e2b(video_bytes)


async def chunk_audio_for_whisper(audio_bytes: bytes, max_bytes: int) -> list[bytes]:
    """Returns segments of audio each <= max_bytes by splitting at ~10min
    boundaries (no re-encode; -c copy). Audio under the threshold returns
    unchanged as [audio_bytes]."""
    if len(audio_bytes) <= max_bytes:
        return [audio_bytes]
    if _HAS_LOCAL_FFMPEG:
        return await _chunk_local(audio_bytes)
    log.info("ffmpeg_using_e2b_fallback", op="chunk")
    return await _chunk_e2b(audio_bytes)


def is_local_ffmpeg_available() -> bool:
    """Lets callers (the runner) emit a better status message when video
    processing will pay the E2B fallback latency penalty."""
    return _HAS_LOCAL_FFMPEG


# --------------------------------------------------------------------------- #
# Local subprocess implementations
# --------------------------------------------------------------------------- #


async def _run_ffmpeg(args: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    return proc.returncode or 0, (stderr or b"").decode("utf-8", errors="replace")


async def _extract_audio_local(video_bytes: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as td:
        in_path = os.path.join(td, "input.bin")
        out_path = os.path.join(td, "audio.mp3")
        with open(in_path, "wb") as f:
            f.write(video_bytes)
        rc, err = await _run_ffmpeg([
            "-y", "-i", in_path,
            "-vn", "-acodec", "libmp3lame", "-b:a", "96k",
            out_path,
        ])
        if rc != 0:
            raise RuntimeError(f"ffmpeg (local) failed: {err[-300:]}")
        with open(out_path, "rb") as f:
            return f.read()


async def _chunk_local(audio_bytes: bytes) -> list[bytes]:
    with tempfile.TemporaryDirectory() as td:
        in_path = os.path.join(td, "input.bin")
        with open(in_path, "wb") as f:
            f.write(audio_bytes)
        rc, err = await _run_ffmpeg([
            "-y", "-i", in_path,
            "-f", "segment", "-segment_time", str(_CHUNK_SECONDS), "-c", "copy",
            os.path.join(td, "chunk_%03d.mp3"),
        ])
        if rc != 0:
            raise RuntimeError(f"ffmpeg chunk (local) failed: {err[-300:]}")
        chunks = []
        for fname in sorted(os.listdir(td)):
            if fname.startswith("chunk_") and fname.endswith(".mp3"):
                with open(os.path.join(td, fname), "rb") as f:
                    chunks.append(f.read())
        return chunks


# --------------------------------------------------------------------------- #
# E2B fallback (one-shot sandbox per call; not the per-run skills sandbox)
# --------------------------------------------------------------------------- #


async def _e2b_sandbox():
    from e2b_code_interpreter import AsyncSandbox
    return await AsyncSandbox.create()


async def _extract_audio_e2b(video_bytes: bytes) -> bytes:
    sandbox = await _e2b_sandbox()
    try:
        await sandbox.files.write("/tmp/input.bin", video_bytes)
        result = await sandbox.commands.run(
            "ffmpeg -y -i /tmp/input.bin -vn -acodec libmp3lame -b:a 96k /tmp/audio.mp3"
        )
        if result.exit_code != 0:
            raise RuntimeError(f"E2B ffmpeg failed: {(result.stderr or '')[-300:]}")
        data = await sandbox.files.read("/tmp/audio.mp3", format="bytes")
        return data if isinstance(data, bytes) else data.encode("latin-1", errors="ignore")
    finally:
        try:
            await sandbox.kill()
        except Exception:  # noqa: BLE001
            pass


async def _chunk_e2b(audio_bytes: bytes) -> list[bytes]:
    sandbox = await _e2b_sandbox()
    try:
        await sandbox.files.write("/tmp/input.bin", audio_bytes)
        result = await sandbox.commands.run(
            f"ffmpeg -y -i /tmp/input.bin -f segment -segment_time {_CHUNK_SECONDS} -c copy /tmp/chunk_%03d.mp3"
        )
        if result.exit_code != 0:
            raise RuntimeError(f"E2B chunk failed: {(result.stderr or '')[-300:]}")
        listing = await sandbox.files.list("/tmp")
        chunks: list[bytes] = []
        for entry in sorted(listing, key=lambda e: e.name):
            if entry.name.startswith("chunk_") and entry.name.endswith(".mp3"):
                data = await sandbox.files.read(f"/tmp/{entry.name}", format="bytes")
                chunks.append(data if isinstance(data, bytes) else data.encode("latin-1", errors="ignore"))
        return chunks
    finally:
        try:
            await sandbox.kill()
        except Exception:  # noqa: BLE001
            pass
