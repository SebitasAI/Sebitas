"""Cloudflare R2 (S3-compatible) access via boto3.

boto3 is synchronous, so every call is wrapped in asyncio.to_thread to keep
the app async end-to-end. Used for two things: agent artifacts (with signed
URLs) and skill packages (SKILL.md + manifest + resources).

Resilience (added after a Slack file ingest of a ~250MB video tripped
`SSLV3_ALERT_BAD_RECORD_MAC` mid-upload):
- Multipart upload for anything above `_MULTIPART_THRESHOLD` (boto3's
  `upload_fileobj` + `TransferConfig`). Each part is its own HTTP request, so
  one corrupted TLS frame retries only that part, not the whole upload.
- Botocore retry config with adaptive mode + 5 attempts. Adaptive backs off
  on rate-related errors instead of hammering R2.
- Explicit retry wrapper on top for transient SSL errors that boto's own
  policy doesn't catch (rare but seen with very large bodies on flaky links).
"""

from __future__ import annotations

import asyncio
import io
import ssl

import boto3
import structlog
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError

from app.config import get_settings

log = structlog.get_logger(__name__)


# Files above this go through multipart upload (each chunk is its own request,
# so a corrupted TLS frame retries only that chunk).
_MULTIPART_THRESHOLD = 25 * 1024 * 1024   # 25 MB
_MULTIPART_CHUNKSIZE = 16 * 1024 * 1024   # 16 MB per part
_MAX_CONCURRENT_PARTS = 4
_MANUAL_RETRY_ATTEMPTS = 3
_MANUAL_RETRY_BACKOFF_S = 1.5


def _client():
    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=f"https://{s.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=s.r2_access_key_id,
        aws_secret_access_key=s.r2_secret_access_key,
        region_name="auto",
        config=Config(
            # Adaptive retry handles throttling / 5xx with exponential backoff.
            # Combined with explicit SSL retry below, gives us 2 layers of safety.
            retries={"max_attempts": 5, "mode": "adaptive"},
            # Larger pool for parallel multipart parts.
            max_pool_connections=20,
        ),
    )


_TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=_MULTIPART_THRESHOLD,
    multipart_chunksize=_MULTIPART_CHUNKSIZE,
    max_concurrency=_MAX_CONCURRENT_PARTS,
    use_threads=True,
)


def _is_transient_ssl_error(exc: BaseException) -> bool:
    """SSL/TLS-level errors that warrant a retry. Includes the
    `BAD_RECORD_MAC` family (corrupted TLS frame, usually a flaky link or
    NIC overload) plus generic timeouts."""
    if isinstance(exc, (ssl.SSLError, EndpointConnectionError)):
        return True
    text = str(exc).lower()
    return any(
        marker in text
        for marker in ("ssl", "bad record mac", "decryption_failed", "tls", "broken pipe")
    )


async def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    """Upload `data` to R2 at `key`. Uses multipart upload for large bodies +
    explicit SSL retry on top of boto's own retries. Idempotent at the R2
    level (PutObject overwrites)."""
    s = get_settings()
    size = len(data)

    def _do_simple():
        _client().put_object(Bucket=s.r2_bucket, Key=key, Body=data, ContentType=content_type)

    def _do_multipart():
        # upload_fileobj uses the TransferConfig multipart settings.
        _client().upload_fileobj(
            Fileobj=io.BytesIO(data),
            Bucket=s.r2_bucket,
            Key=key,
            ExtraArgs={"ContentType": content_type},
            Config=_TRANSFER_CONFIG,
        )

    op = _do_multipart if size >= _MULTIPART_THRESHOLD else _do_simple

    last_exc: BaseException | None = None
    for attempt in range(1, _MANUAL_RETRY_ATTEMPTS + 1):
        try:
            await asyncio.to_thread(op)
            if attempt > 1:
                log.info("r2_put_succeeded_after_retry", key=key, attempt=attempt, size=size)
            return
        except ClientError as exc:
            # Botocore's own retry already exhausted; don't retry again.
            log.warning("r2_put_client_error", key=key, attempt=attempt, error=str(exc)[:200])
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < _MANUAL_RETRY_ATTEMPTS and _is_transient_ssl_error(exc):
                wait = _MANUAL_RETRY_BACKOFF_S * (2 ** (attempt - 1))
                log.warning(
                    "r2_put_transient_ssl_retry",
                    key=key, attempt=attempt, wait=wait, error=str(exc)[:200],
                )
                await asyncio.sleep(wait)
                continue
            raise
    # Defensive: shouldn't reach here.
    if last_exc:
        raise last_exc


async def get_bytes(key: str) -> bytes:
    s = get_settings()

    def _do() -> bytes:
        resp = _client().get_object(Bucket=s.r2_bucket, Key=key)
        return resp["Body"].read()

    return await asyncio.to_thread(_do)


async def get_text(key: str) -> str:
    return (await get_bytes(key)).decode("utf-8")


async def presigned_get(key: str) -> str:
    s = get_settings()

    def _do() -> str:
        return _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": s.r2_bucket, "Key": key},
            ExpiresIn=s.artifact_url_expiry,
        )

    return await asyncio.to_thread(_do)


async def upload_and_sign(key: str, data: bytes, content_type: str) -> str:
    """Upload an artifact and return a signed GET URL."""
    await put_bytes(key, data, content_type)
    return await presigned_get(key)
