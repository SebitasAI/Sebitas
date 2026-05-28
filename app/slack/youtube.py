"""YouTube URL detection in Slack message text + transcript fetch.

Two halves:
- `extract_video_ids(text)`: regex over the message text, deduped, returns
  canonical `{video_id, url}` pairs. Handles Slack mrkdwn wrapping (`<url>`,
  `<url|label>`) by stripping the angle brackets before matching.
- `fetch_transcript(video_id)`: uses youtube-transcript-api (pure Python,
  added as a small dep). Sync underneath, called via asyncio.to_thread.
- `fetch_metadata(video_id)`: title + channel via YouTube's public oEmbed
  (no auth). Best-effort; duration_s falls back to last-segment time.

Honest flag: youtube-transcript-api wraps YouTube's public caption endpoints,
which YouTube has changed several times. If captions stop working, the v3
Data API + OAuth is the heavier-weight fallback (not built here).
"""

from __future__ import annotations

import asyncio
import re

import aiohttp
import structlog

log = structlog.get_logger(__name__)

# Strict 11-char video_id charset matches YouTube's actual format. Without
# this strictness an unrelated URL like `youtube.com/about` would false-match.
_YT_RE = re.compile(
    r"""
    (?:https?://)?
    (?:www\.|m\.)?
    (?:
        youtube\.com/(?:watch\?(?:[^\s>]*?&)?v=|shorts/|live/|embed/)
      | youtu\.be/
    )
    ([A-Za-z0-9_-]{11})
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Slack wraps URLs as `<https://example.com>` or `<https://example.com|label>`.
_SLACK_URL_WRAPPER_RE = re.compile(r"<((?:https?://)?[^|>\s]+)(?:\|[^>]*)?>")


def extract_video_ids(text: str) -> list[dict]:
    """Returns one entry per UNIQUE video_id seen, in arrival order:
    `[{video_id, url}]`. Empty list if `text` has no YouTube URLs."""
    if not text:
        return []
    unwrapped = _SLACK_URL_WRAPPER_RE.sub(r"\1", text)
    seen: set[str] = set()
    out: list[dict] = []
    for m in _YT_RE.finditer(unwrapped):
        vid = m.group(1)
        if vid in seen:
            continue
        seen.add(vid)
        out.append({"video_id": vid, "url": f"https://www.youtube.com/watch?v={vid}"})
    return out


class _NoCaptionsError(RuntimeError):
    """Raised when a YouTube video has no captions available. Mapped by the
    caller to a polite user-facing message."""


async def fetch_transcript(video_id: str, languages: list[str] | None = None) -> dict:
    """Returns `{transcript, language, duration_s, segments_count}` for the
    video. Raises `_NoCaptionsError` if captions are disabled or no caption
    track is found in any of the requested languages."""
    langs = languages or ["es", "en", "pt"]

    def _sync_fetch():
        # youtube-transcript-api v1.x: instance-based API. v0.x had class-level
        # `get_transcript`; we target the v1+ shape since that's what pip ships
        # today. If captions get pulled, NoTranscriptFound surfaces.
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            from youtube_transcript_api._errors import (
                NoTranscriptFound, TranscriptsDisabled, VideoUnavailable,
            )
        except ImportError as exc:
            raise RuntimeError("youtube-transcript-api missing; run uv sync") from exc

        api = YouTubeTranscriptApi()
        try:
            fetched = api.fetch(video_id, languages=langs)
        except (TranscriptsDisabled, NoTranscriptFound) as exc:
            raise _NoCaptionsError(str(exc)) from exc
        except VideoUnavailable as exc:
            raise RuntimeError(f"video unavailable: {exc}") from exc

        snippets = list(fetched.snippets)
        text = " ".join((s.text or "").strip() for s in snippets).strip()
        last = snippets[-1] if snippets else None
        duration_s = int(((last.start or 0) + (last.duration or 0)) if last else 0)
        return {
            "transcript": text,
            "language": getattr(fetched, "language_code", None) or langs[0],
            "duration_s": duration_s,
            "segments_count": len(snippets),
        }

    return await asyncio.to_thread(_sync_fetch)


async def fetch_metadata(video_id: str) -> dict:
    """Best-effort title + channel via YouTube's public oEmbed endpoint.
    Returns `{}` on any failure -- metadata is nice-to-have, not blocking."""
    url = (
        "https://www.youtube.com/oembed?"
        f"url=https://www.youtube.com/watch?v={video_id}&format=json"
    )
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return {}
                data = await resp.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        log.warning("yt_oembed_failed", video_id=video_id, error=str(exc))
        return {}
    return {
        "title": data.get("title"),
        "channel": data.get("author_name"),
        "channel_url": data.get("author_url"),
        "thumbnail_url": data.get("thumbnail_url"),
    }


# Re-export for callers that want to differentiate "no captions" from other errors.
NoCaptionsError = _NoCaptionsError
