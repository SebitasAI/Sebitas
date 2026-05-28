"""Cloudflare R2 (S3-compatible) access via boto3.

boto3 is synchronous, so every call is wrapped in asyncio.to_thread to keep the
app async end-to-end. Used for two things: agent artifacts (with signed URLs)
and skill packages (SKILL.md + manifest + resources).
"""

from __future__ import annotations

import asyncio

import boto3
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)


def _client():
    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=f"https://{s.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=s.r2_access_key_id,
        aws_secret_access_key=s.r2_secret_access_key,
        region_name="auto",
    )


async def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    s = get_settings()

    def _do():
        _client().put_object(Bucket=s.r2_bucket, Key=key, Body=data, ContentType=content_type)

    await asyncio.to_thread(_do)


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
