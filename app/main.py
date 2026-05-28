"""Entrypoint: FastAPI app. The Slack Socket Mode handler runs as a background
connection started in the lifespan. Single process, 12-factor."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agent.claude import flush_langfuse
from app.agent.graph import build_graph, set_graph
from app.concurrency import cleanup_old_events
from app.config import get_settings
from app.db.engine import engine
from app.integrations.webhook import router as pipedream_webhook_router
from app.logging import configure_logging
from app.slack.app import build_app, build_socket_handler

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    settings = get_settings()
    # psycopg (the LangGraph checkpointer's driver) understands the raw libpq URL
    # (sslmode etc.) directly, so pass DATABASE_URL as-is here.
    async with AsyncPostgresSaver.from_conn_string(settings.database_url) as checkpointer:
        await checkpointer.setup()
        set_graph(build_graph(checkpointer))
        log.info("agent_graph_ready")

        slack_app = build_app()
        handler = build_socket_handler(slack_app)
        await handler.connect_async()
        log.info("slack_socket_mode_connected")

        # Background cleanup: prune slack_event_seen rows older than 1h every
        # 5 min, so the dedupe table doesn't grow unbounded.
        async def _event_cleanup_loop():
            while True:
                try:
                    n = await cleanup_old_events(older_than_hours=1)
                    if n:
                        log.info("slack_event_cleanup", removed=n)
                except Exception as exc:  # noqa: BLE001
                    log.warning("slack_event_cleanup_failed", error=str(exc))
                await asyncio.sleep(300)
        cleanup_task = asyncio.create_task(_event_cleanup_loop())

        try:
            yield
        finally:
            cleanup_task.cancel()
            try:
                await handler.close_async()
            except Exception as exc:  # noqa: BLE001
                log.warning("slack_disconnect_failed", error=str(exc))
            await engine.dispose()
            flush_langfuse()
            log.info("shutdown_complete")


app = FastAPI(title="Sebitas", lifespan=lifespan)
app.include_router(pipedream_webhook_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    from app.config import get_settings

    get_settings()  # fail fast if required env vars are missing
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
