"""Backfill `agent_run` rows from Langfuse traces.

The agent_run table (migration 0030) is brand new. For runs going
forward, `runner.py` writes one row per invocation at finalize time.
For HISTORICAL runs, the cost data only exists in Langfuse (as scores
`total_cost_usd` and `sales_cost_usd` we emitted per trace). This
script reads every trace via the Langfuse SDK and replays it into our
DB so the `/usage` dashboard shows real numbers from day 1.

Mapping:
  - `workspace:<name>` tag        -> workspace.name -> workspace_id
  - user_id field (email or actor) -> app_user.email or sentinel
  - `origin:<kind>` tag + actor    -> kind (slack_thread / scheduled_task / automation)
  - scores `sales_cost_usd` / `total_cost_usd` -> agent_run.*_cost_usd
  - observation.usage (sum)       -> input_tokens / output_tokens
  - timestamp                     -> started_at; latency adds finished_at

Idempotent: skips traces whose `langfuse_trace_id` already exists in
agent_run. Re-running picks up only new traces since last run.

Usage:
  doppler run -p sebitas -c prd -- python -m scripts.backfill_agent_run
  doppler run -p sebitas -c prd -- python -m scripts.backfill_agent_run --since-days 30
  doppler run -p sebitas -c prd -- python -m scripts.backfill_agent_run --workspace Antiff
  doppler run -p sebitas -c prd -- python -m scripts.backfill_agent_run --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from langfuse import get_client
from sqlalchemy import select

from app.db.models import AgentRun, AppUser, Workspace
from app.db.session import get_session
from app.logging import configure_logging

log = structlog.get_logger("backfill_agent_run")


# Sentinel actor strings the agent uses for system-fired runs.
# Match these against trace.user_id to detect the run kind.
SYSTEM_SCHEDULED = "SYSTEM_SCHEDULED"
SYSTEM_AUTOMATION = "SYSTEM_AUTOMATION"


def _parse_workspace_from_tags(tags: list[str] | None) -> str | None:
    """Extract the workspace name from a list of trace tags. Returns
    None if the `workspace:<name>` tag is absent (older traces)."""
    if not tags:
        return None
    for t in tags:
        if t.startswith("workspace:"):
            return t.split(":", 1)[1].strip() or None
    return None


def _parse_origin_from_tags(tags: list[str] | None) -> str | None:
    if not tags:
        return None
    for t in tags:
        if t.startswith("origin:"):
            return t.split(":", 1)[1].strip() or None
    return None


def _derive_kind(user_id: str | None, origin_tag: str | None) -> str:
    """Map trace metadata to agent_run.kind. Order matters:
    actor sentinels are stronger evidence than the origin tag (which
    didn't distinguish automation from slack_message in older code).
    Defaults to slack_thread when nothing matches -- the common case."""
    if user_id == SYSTEM_SCHEDULED:
        return "scheduled_task"
    if user_id == SYSTEM_AUTOMATION:
        return "automation"
    if origin_tag == "scheduled_task":
        return "scheduled_task"
    return "slack_thread"


async def _build_workspace_map() -> dict[str, Any]:
    """Workspace name -> id. We match by the `name` column populated
    on install. Returns lowercased keys for case-insensitive matches."""
    async with get_session() as session:
        rows = (
            await session.execute(select(Workspace.id, Workspace.name))
        ).all()
    out: dict[str, Any] = {}
    for r in rows:
        if r.name:
            out[r.name.lower()] = r.id
    return out


async def _build_user_map() -> dict[tuple[Any, str], Any]:
    """(workspace_id, lowercased email) -> app_user_id. We pull email
    from slack_user (it's not on AppUser directly), so the lookup is a
    join across (workspace_id, slack_user_id, email)."""
    from app.db.models import SlackUser

    async with get_session() as session:
        rows = (
            await session.execute(
                select(
                    AppUser.id,
                    AppUser.workspace_id,
                    SlackUser.email,
                ).join(
                    SlackUser,
                    (SlackUser.workspace_id == AppUser.workspace_id)
                    & (SlackUser.slack_user_id == AppUser.slack_user_id),
                )
            )
        ).all()
    out: dict[tuple[Any, str], Any] = {}
    for r in rows:
        if r.email:
            out[(r.workspace_id, r.email.lower())] = r.id
    return out


async def _build_slack_uid_to_workspace_map() -> dict[str, Any]:
    """Slack U-id -> workspace_id. For traces whose user_id is the raw
    Slack U-id instead of an email (older code that fell through to
    slack_user_id when email was missing). Many U-ids appear in a
    single workspace, so this is unambiguous; if it appears in 2+,
    we keep the first (no good way to disambiguate from a trace).

    Includes the sentinel actors SYSTEM_SCHEDULED / SYSTEM_AUTOMATION
    -- those are per-workspace app_user rows too."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(AppUser.slack_user_id, AppUser.workspace_id)
                .order_by(AppUser.created_at.desc())
            )
        ).all()
    out: dict[str, Any] = {}
    for r in rows:
        if r.slack_user_id and r.slack_user_id not in out:
            out[r.slack_user_id] = r.workspace_id
    return out


async def _build_email_to_workspace_map() -> dict[str, Any]:
    """Fallback: email -> workspace_id. For older traces missing the
    `workspace:<name>` tag, we infer the workspace from the user's
    email (which IS on the trace as `user_id`). When the same email
    exists in multiple workspaces, we pick the most recently created
    one -- imperfect but better than dropping the row entirely.

    Bot users / sentinel actors (SYSTEM_SCHEDULED / SYSTEM_AUTOMATION)
    won't have emails, so this fallback only helps human Slack users."""
    from app.db.models import SlackUser

    async with get_session() as session:
        rows = (
            await session.execute(
                select(
                    AppUser.workspace_id,
                    SlackUser.email,
                    AppUser.created_at,
                )
                .join(
                    SlackUser,
                    (SlackUser.workspace_id == AppUser.workspace_id)
                    & (SlackUser.slack_user_id == AppUser.slack_user_id),
                )
                .order_by(AppUser.created_at.desc())
            )
        ).all()
    out: dict[str, Any] = {}
    for r in rows:
        if r.email:
            key = r.email.lower()
            # First win = most recent (order_by desc).
            if key not in out:
                out[key] = r.workspace_id
    return out


async def _existing_trace_ids() -> set[str]:
    """All langfuse_trace_id values currently in agent_run. Used for
    dedup so the script is idempotent."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(AgentRun.langfuse_trace_id).where(
                    AgentRun.langfuse_trace_id.isnot(None)
                )
            )
        ).all()
    return {r[0] for r in rows if r[0]}


def _tokens_from_observations(obs: list[Any]) -> tuple[int, int, dict[str, dict]]:
    """Sum input/output tokens across all generation observations of a
    trace. Also build a (model -> {input, output, usd}) breakdown for
    the agent_run.by_model column. Skips non-GENERATION observations."""
    in_total = 0
    out_total = 0
    by_model: dict[str, dict[str, float | int]] = {}
    for o in obs:
        if getattr(o, "type", None) != "GENERATION":
            continue
        u = getattr(o, "usage", None)
        if u is None:
            continue
        ti = int(getattr(u, "input", 0) or 0)
        to = int(getattr(u, "output", 0) or 0)
        in_total += ti
        out_total += to
        model = getattr(o, "model", None) or "unknown"
        bucket = by_model.setdefault(model, {"input": 0, "output": 0, "usd": 0.0})
        bucket["input"] = int(bucket["input"]) + ti  # type: ignore[arg-type]
        bucket["output"] = int(bucket["output"]) + to  # type: ignore[arg-type]
    return in_total, out_total, by_model


def _score_value(scores: list[Any] | None, name: str) -> float | None:
    """Find a score by name; return its value or None."""
    if not scores:
        return None
    for s in scores:
        if getattr(s, "name", None) == name:
            v = getattr(s, "value", None)
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None
    return None


async def backfill(
    *,
    since: datetime | None,
    workspace_filter: str | None,
    dry_run: bool,
    page_limit: int,
) -> dict[str, int]:
    """Walk Langfuse traces, insert agent_run rows. Returns a stats dict."""
    configure_logging()
    lf = get_client()

    workspace_map = await _build_workspace_map()
    user_map = await _build_user_map()
    email_to_ws = await _build_email_to_workspace_map()
    uid_to_ws = await _build_slack_uid_to_workspace_map()
    existing = await _existing_trace_ids()
    log.info(
        "backfill_starting",
        workspaces=len(workspace_map),
        users=len(user_map),
        existing_rows=len(existing),
        since=since.isoformat() if since else "all-time",
        dry_run=dry_run,
    )

    stats = {
        "fetched": 0,
        "skipped_existing": 0,
        "skipped_no_workspace": 0,
        "skipped_workspace_filter": 0,
        "skipped_no_cost": 0,
        "inserted": 0,
        "errors": 0,
    }

    page = 1
    while True:
        try:
            kwargs: dict[str, Any] = {"limit": page_limit, "page": page}
            if since is not None:
                kwargs["from_timestamp"] = since
            response = lf.api.trace.list(**kwargs)
        except Exception as exc:  # noqa: BLE001
            log.warning("trace_list_failed", page=page, error=str(exc))
            stats["errors"] += 1
            break

        batch = list(getattr(response, "data", []) or [])
        if not batch:
            break

        stats["fetched"] += len(batch)
        for t in batch:
            try:
                trace_id = t.id
                if trace_id in existing:
                    stats["skipped_existing"] += 1
                    continue
                ws_name = _parse_workspace_from_tags(t.tags)
                ws_id = workspace_map.get((ws_name or "").lower()) if ws_name else None
                # Fallback: older traces lack the workspace tag. Use the
                # user_id field to map back. user_id can be email
                # (newer), Slack U-id (older), or a SYSTEM_* actor.
                user_id_field = getattr(t, "user_id", None) or ""
                if ws_id is None:
                    if "@" in user_id_field:
                        ws_id = email_to_ws.get(user_id_field.lower())
                if ws_id is None and user_id_field:
                    ws_id = uid_to_ws.get(user_id_field)
                if ws_id is None:
                    stats["skipped_no_workspace"] += 1
                    continue
                if workspace_filter and (ws_name or "").lower() != workspace_filter.lower():
                    stats["skipped_workspace_filter"] += 1
                    continue

                # Fetch the full trace to get scores + observations.
                full = lf.api.trace.get(trace_id)
                scores = getattr(full, "scores", None) or []
                obs = getattr(full, "observations", None) or []

                sales_cost = _score_value(scores, "sales_cost_usd")
                total_cost = _score_value(scores, "total_cost_usd")
                in_tokens, out_tokens, by_model = _tokens_from_observations(obs)

                # If we have token counts but no cost score (older trace
                # before we emitted scores), compute cost from tokens.
                if total_cost is None and (in_tokens or out_tokens):
                    # Use Opus pricing as default since most historical
                    # runs used Opus. Underestimates cost for runs that
                    # used cheaper models -- acceptable for a backfill.
                    total_cost = (in_tokens / 1_000_000) * 5.0 + (
                        out_tokens / 1_000_000
                    ) * 25.0
                if sales_cost is None and total_cost is not None:
                    sales_cost = total_cost * 5.0  # current SALES_COST_MULTIPLIER

                if (sales_cost or 0) <= 0 and (in_tokens or 0) == 0:
                    stats["skipped_no_cost"] += 1
                    continue

                origin = _parse_origin_from_tags(t.tags)
                user_id_field = getattr(t, "user_id", None) or ""
                kind = _derive_kind(user_id_field, origin)

                # Resolve app_user_id by email (when the trace's user_id
                # field is an email). For sentinel actors, leave null.
                app_user_id = None
                if user_id_field and "@" in user_id_field:
                    app_user_id = user_map.get(
                        (ws_id, user_id_field.lower())
                    )

                started_at = t.timestamp
                if started_at and started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
                latency_ms = getattr(t, "latency", None) or 0
                # latency is in seconds in Langfuse v3 (was ms historically).
                # Try to detect: > 60 likely ms, otherwise treat as seconds.
                duration_s = float(latency_ms) if latency_ms < 60 else float(latency_ms) / 1000.0
                finished_at = (
                    started_at + timedelta(seconds=duration_s)
                    if started_at
                    else None
                )

                if dry_run:
                    stats["inserted"] += 1
                    continue

                async with get_session() as session:
                    run = AgentRun(
                        workspace_id=ws_id,
                        app_user_id=app_user_id,
                        kind=kind,
                        parent_ref_id=None,  # Can't recover from Langfuse alone
                        parent_name_snapshot=None,
                        slack_channel_id=None,
                        slack_thread_ts=None,
                        input_tokens=in_tokens,
                        output_tokens=out_tokens,
                        cache_read_tokens=0,
                        cache_write_tokens=0,
                        total_cost_usd=round(total_cost or 0.0, 6),
                        sales_cost_usd=round(sales_cost or 0.0, 6),
                        by_model=by_model,
                        status="success",  # We don't store status in Langfuse
                        langfuse_trace_id=trace_id,
                        error=None,
                        started_at=started_at,
                        finished_at=finished_at or started_at,
                    )
                    session.add(run)
                    await session.commit()
                existing.add(trace_id)
                stats["inserted"] += 1
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "trace_backfill_failed",
                    trace_id=getattr(t, "id", "?"),
                    error=str(exc)[:200],
                )
                stats["errors"] += 1

        log.info(
            "backfill_page_done",
            page=page,
            batch_size=len(batch),
            **stats,
        )
        if len(batch) < page_limit:
            break
        page += 1

    return stats


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--since-days",
        type=int,
        default=None,
        help="Only backfill traces newer than N days. Default: all-time.",
    )
    p.add_argument(
        "--workspace",
        type=str,
        default=None,
        help="Only this workspace name (case-insensitive). Default: all.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Count what would be inserted; do not write to the DB.",
    )
    p.add_argument(
        "--page-limit",
        type=int,
        default=100,
        help="Page size for the Langfuse trace.list call (max 100).",
    )
    return p.parse_args()


async def main() -> None:
    args = _parse_args()
    since = (
        datetime.now(timezone.utc) - timedelta(days=args.since_days)
        if args.since_days
        else None
    )
    stats = await backfill(
        since=since,
        workspace_filter=args.workspace,
        dry_run=args.dry_run,
        page_limit=args.page_limit,
    )
    print("\n=== backfill stats ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())
