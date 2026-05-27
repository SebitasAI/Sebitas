"""Entrypoint: FastAPI app. The Slack Socket Mode handler runs as a background
connection started in the lifespan. Single process, 12-factor."""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.agent.claude import flush_langfuse
from app.db.engine import engine
from app.logging import configure_logging
from app.slack.app import build_app, build_socket_handler

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    slack_app = build_app()
    handler = build_socket_handler(slack_app)
    await handler.connect_async()
    log.info("slack_socket_mode_connected")
    try:
        yield
    finally:
        try:
            await handler.close_async()
        except Exception as exc:  # noqa: BLE001
            log.warning("slack_disconnect_failed", error=str(exc))
        await engine.dispose()
        flush_langfuse()
        log.info("shutdown_complete")


app = FastAPI(title="Sebitas", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    from app.config import get_settings

    get_settings()  # fail fast if required env vars are missing
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
