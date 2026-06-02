"""Entrypoint: FastAPI app. The Slack Socket Mode handler runs as a background
connection started in the lifespan. Single process, 12-factor."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agent.claude import flush_langfuse
from app.agent.graph import build_graph, set_graph
from app.concurrency import cleanup_old_events
from app.config import get_settings
from app.db.engine import engine
from app.integrations.connect import (
    periodic_resume_loop as integration_resume_loop,
    resume_pending_polls,
)
from app.api.integrations import router as integrations_router
from app.api.admin import router as admin_router
from app.api.automations import router as automations_router
from app.api.scheduled_tasks import router as scheduled_tasks_router
from app.api.skills import router as skills_router
from app.api.team import router as team_router
from app.auth.clerk_provisioning import provision_and_backfill_all_workspaces
from app.integrations.webhook import router as pipedream_webhook_router
from app.logging import configure_logging
from app.automations.events import start_consumer as start_automation_consumer
from app.automations.events import stop_consumer as stop_automation_consumer
from app.memory.compaction import run_compaction_loop as run_memory_compaction_loop
from app.scheduled_tasks.repository import seed_system_tasks_for_all_workspaces
from app.scheduled_tasks.scheduler import run_scheduler_loop
from app.skills.preview_store import cleanup_expired as cleanup_expired_previews
from app.slack.app import build_socket_handler, init_slack_app
from app.slack.roster import periodic_refresh_loop as roster_periodic
from app.spaces.api import router as spaces_internal_router
from app.web_api import router as web_api_router
from app.spaces.convex_backend import ConvexSharedSpaceBackend
from app.spaces.gateway import set_backend as set_space_backend

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

        # Spaces backend: ConvexShared if the env is configured, else Mock.
        # We don't fail-fast on missing Convex env -- a deployment can be in
        # transition (Convex project being set up); MockSpaceBackend keeps
        # deploy_space + list_spaces + delete_space working against Postgres
        # while the URL just stays a placeholder.
        if settings.convex_url and settings.convex_deploy_key:
            set_space_backend(ConvexSharedSpaceBackend(
                convex_url=settings.convex_url,
                deploy_key=settings.convex_deploy_key,
                hosting_site_url=settings.convex_hosting_site_url,
            ))
            log.info("spaces_backend", impl="convex-shared")
        else:
            log.info("spaces_backend", impl="mock", reason="missing convex env")

        slack_app = init_slack_app()
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

        # Slack roster periodic refresh: every 12h reruns users.list per
        # workspace. Lazy sync also happens per-run via ensure_workspace_synced.
        roster_task = asyncio.create_task(roster_periodic())

        # Skill preview sweep: delete expired skill_preview rows every 5 min.
        # Previews have a 30-min TTL; a few minutes of staleness is fine, so
        # the loop is cheap (single DELETE WHERE expires_at < now()).
        async def _preview_cleanup_loop():
            while True:
                try:
                    n = await cleanup_expired_previews()
                    if n:
                        log.info("skill_preview_cleanup", removed=n)
                except Exception as exc:  # noqa: BLE001
                    log.warning("skill_preview_cleanup_failed", error=str(exc))
                await asyncio.sleep(300)
        preview_cleanup_task = asyncio.create_task(_preview_cleanup_loop())

        # Restart any in-memory poll tasks that died on a previous process exit.
        # Pending integration connect flows (row.status='pending' with a
        # pending_run_id) need a live `_poll` to flip them to 'connected' once
        # the user finishes OAuth on the provider's side. Without this, a
        # Render redeploy mid-OAuth strands the row forever and the next user
        # message triggers a reinstall loop.
        try:
            resumed = await resume_pending_polls()
            if resumed:
                log.info("connect_polls_resumed_on_startup", count=resumed)
        except Exception as exc:  # noqa: BLE001
            log.warning("resume_pending_polls_failed", error=str(exc))
        integration_resume_task = asyncio.create_task(integration_resume_loop())

        # Scheduled tasks (slice T-1): idempotently re-seed the two system
        # tasks for every installed workspace at startup, then spawn the
        # scheduler loop that fires due tasks on a 30s tick. Seeding is
        # ON CONFLICT-safe so concurrent replicas don't duplicate rows.
        try:
            inserted = await seed_system_tasks_for_all_workspaces()
            if inserted:
                log.info("scheduled_task_seed_on_startup", inserted=inserted)
        except Exception as exc:  # noqa: BLE001
            log.warning("scheduled_task_seed_startup_failed", error=str(exc))
        scheduled_task_loop = asyncio.create_task(run_scheduler_loop())

        # Automations consumer (event-driven hooks). Spawns a single task
        # that pulls events off the in-process queue and routes them. See
        # app/automations/events.py for the queue + lifecycle. Cancelled
        # in the shutdown branch below.
        start_automation_consumer()
        log.info("automation_consumer_started")

        # Memory compaction (slice T-X Phase C). One background task per
        # process that walks all workspaces' memory skills every 24h,
        # rewrites the curated summary integrating new observations, and
        # clears the log so bodies stay well under the 200KB cap.
        memory_compaction_task = asyncio.create_task(run_memory_compaction_loop())

        # Clerk Organizations backfill (slice T-5). Idempotent: for each
        # installed workspace without a clerk_org_id, provision one and
        # link existing AppUsers as members. Once all rows are linked this
        # is a quick no-op (only DB queries, no Clerk API calls). Failure
        # is logged but never blocks startup -- the web-side endpoint can
        # re-trigger provisioning on first login.
        try:
            counts = await provision_and_backfill_all_workspaces()
            if counts.get("orgs_created") or counts.get("members_linked"):
                log.info("clerk_org_backfill_on_startup", **counts)
        except Exception as exc:  # noqa: BLE001
            log.warning("clerk_org_backfill_startup_failed", error=str(exc))

        try:
            yield
        finally:
            cleanup_task.cancel()
            roster_task.cancel()
            preview_cleanup_task.cancel()
            integration_resume_task.cancel()
            scheduled_task_loop.cancel()
            memory_compaction_task.cancel()
            try:
                await stop_automation_consumer()
            except Exception as exc:  # noqa: BLE001
                log.warning("automation_consumer_stop_failed", error=str(exc))
            try:
                await handler.close_async()
            except Exception as exc:  # noqa: BLE001
                log.warning("slack_disconnect_failed", error=str(exc))
            await engine.dispose()
            flush_langfuse()
            log.info("shutdown_complete")


app = FastAPI(title="Misterr", lifespan=lifespan)

# CORS: the Misterr web app (slice T-2) calls the new /api/scheduled-tasks/*
# endpoints directly from the browser with a Bearer Clerk JWT. The legacy
# /api/web/* endpoints don't need CORS (they're server-to-server with a
# shared secret, never browser-direct), but the middleware is global so they
# inherit it harmlessly.
_cors_origins = [o.strip() for o in get_settings().frontend_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Misterr-Workspace-Id"],
)

app.include_router(pipedream_webhook_router)
app.include_router(spaces_internal_router)
app.include_router(web_api_router)
app.include_router(scheduled_tasks_router)
app.include_router(automations_router)
app.include_router(skills_router)
app.include_router(admin_router)
app.include_router(team_router)
app.include_router(integrations_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# Slack OAuth endpoints (slice C1). Bolt's OAuth handlers live inside the
# AsyncApp; this thin adapter routes HTTP /slack/install + /slack/oauth_redirect
# through them. Events keep flowing via Socket Mode in the lifespan above.
# When SLACK_CLIENT_ID/SECRET/SIGNING_SECRET aren't set, init_slack_app builds
# without oauth_settings and these endpoints return 404 -- that's fine for
# CLI-install pilots.
from fastapi import Request  # noqa: E402

from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler  # noqa: E402


_slack_request_handler: AsyncSlackRequestHandler | None = None


def _get_slack_request_handler() -> AsyncSlackRequestHandler:
    global _slack_request_handler
    if _slack_request_handler is None:
        _slack_request_handler = AsyncSlackRequestHandler(init_slack_app())
    return _slack_request_handler


@app.get("/slack/install")
async def slack_install(req: Request):
    return await _get_slack_request_handler().handle(req)


@app.get("/slack/oauth_redirect")
async def slack_oauth_redirect(req: Request):
    return await _get_slack_request_handler().handle(req)


if __name__ == "__main__":
    import uvicorn

    from app.config import get_settings

    get_settings()  # fail fast if required env vars are missing
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
