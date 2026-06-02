"""REST endpoints for the Misterr web app's Usage dashboard.

Four tabs in the UI map 1:1 to the four endpoints below:

  GET /api/usage/overview         -> Overview tab
  GET /api/usage/team             -> Team tab
  GET /api/usage/activity         -> Activity feed (paginated)
  GET /api/usage/scheduled-tasks  -> Per-task summary

All queries are workspace-scoped via `require_app_user`. All aggregates
read from `agent_run` exclusively -- the rest of the schema is joined
in for display strings (user names, task names) only.

Credit unit: 1 credit = $0.001 sales_cost_usd. See `app/agent/cost.py`
for the multiplier rationale (currently 5x = 80% gross margin)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, select

from app.auth.clerk import ResolvedAppUser, require_app_user
from app.db.models import AgentRun, AppUser, ScheduledTask
from app.db.session import get_session

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/usage", tags=["usage"])


# Customer-visible cost basis. Kept in sync with app/agent/cost.py's
# CREDITS_PER_USD; duplicated here so the API surface doesn't import
# from the agent module just for a constant.
CREDITS_PER_USD = 1000.0


# --------------------------------------------------------------------------- #
# Date-range helpers
# --------------------------------------------------------------------------- #


VALID_RANGES = ("7d", "30d", "90d", "all")


def _range_to_cutoff(range_: str) -> datetime | None:
    """Map the range query param to a UTC cutoff datetime, or None for
    'all time'."""
    if range_ == "7d":
        return datetime.now(timezone.utc) - timedelta(days=7)
    if range_ == "30d":
        return datetime.now(timezone.utc) - timedelta(days=30)
    if range_ == "90d":
        return datetime.now(timezone.utc) - timedelta(days=90)
    if range_ == "all":
        return None
    raise HTTPException(
        status_code=400, detail=f"range must be one of {VALID_RANGES}"
    )


def _usd_to_credits(usd: float | None) -> int:
    """Convert sales_cost_usd to integer credits. Rounded, since the
    UI displays whole numbers."""
    if usd is None:
        return 0
    return int(round(float(usd) * CREDITS_PER_USD))


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #


class OverviewDailyBucket(BaseModel):
    date: str  # YYYY-MM-DD
    threads: int
    scheduled_tasks: int
    automations: int
    media: int


class OverviewTopUser(BaseModel):
    app_user_id: str | None
    display_name: str
    credits: int


class OverviewTopScheduledTask(BaseModel):
    task_id: str | None
    name: str
    credits: int


class OverviewResponse(BaseModel):
    total_credits: int
    burn_per_day: int
    days_in_range: int
    daily: list[OverviewDailyBucket]
    category_pct: dict[str, float]  # 'threads' -> 83.0 etc.
    top_users: list[OverviewTopUser]
    top_scheduled_tasks: list[OverviewTopScheduledTask]


class TeamRow(BaseModel):
    app_user_id: str | None
    display_name: str
    threads: int
    scheduled_task_runs: int
    automation_runs: int
    credits: int
    last_activity: datetime | None


class TeamResponse(BaseModel):
    total_users: int
    rows: list[TeamRow]


class ActivityRow(BaseModel):
    id: str
    kind: str
    parent_name: str | None
    user_display_name: str
    user_id: str | None
    credits: int
    started_at: datetime
    slack_channel_id: str | None
    slack_thread_ts: str | None
    status: str


class ActivityResponse(BaseModel):
    rows: list[ActivityRow]
    page: int
    page_size: int
    total_count: int


class ScheduledTaskRow(BaseModel):
    task_id: str | None
    name: str
    total_runs: int
    last_activity: datetime | None
    created_by_user_id: str | None
    created_by_display_name: str
    total_credits: int
    is_system: bool


class ScheduledTaskResponse(BaseModel):
    rows: list[ScheduledTaskRow]


# --------------------------------------------------------------------------- #
# Display-name resolution
# --------------------------------------------------------------------------- #


async def _user_display_names(
    session, app_user_ids: list[Any]
) -> dict[Any, str]:
    """Bulk-resolve (app_user_id -> display name). Joins to SlackUser
    for the human-friendly name; falls back to slack_user_id, then
    'Unknown user' for nulls."""
    if not app_user_ids:
        return {}
    # AppUser -> SlackUser via (workspace_id, slack_user_id). We pull
    # both fields and let the caller default.
    from app.db.models import SlackUser

    rows = (
        await session.execute(
            select(
                AppUser.id,
                AppUser.slack_user_id,
                SlackUser.display_name,
                SlackUser.real_name,
            )
            .join(
                SlackUser,
                and_(
                    SlackUser.workspace_id == AppUser.workspace_id,
                    SlackUser.slack_user_id == AppUser.slack_user_id,
                ),
                isouter=True,
            )
            .where(AppUser.id.in_(app_user_ids))
        )
    ).all()
    out: dict[Any, str] = {}
    for r in rows:
        name = r.display_name or r.real_name or r.slack_user_id or "Unknown user"
        out[r.id] = name
    return out


# --------------------------------------------------------------------------- #
# GET /api/usage/overview
# --------------------------------------------------------------------------- #


@router.get("/overview", response_model=OverviewResponse)
async def get_overview(
    range: Literal["7d", "30d", "90d", "all"] = "30d",
    user: ResolvedAppUser = Depends(require_app_user),
) -> OverviewResponse:
    cutoff = _range_to_cutoff(range)
    ws_id = user.workspace_id

    base_filter = [AgentRun.workspace_id == ws_id]
    if cutoff is not None:
        base_filter.append(AgentRun.started_at >= cutoff)

    async with get_session() as session:
        # Total spend (sum of sales_cost_usd in window).
        total_usd = (
            await session.execute(
                select(func.coalesce(func.sum(AgentRun.sales_cost_usd), 0))
                .where(*base_filter)
            )
        ).scalar_one()
        total_credits = _usd_to_credits(total_usd)

        # Daily buckets, broken down by kind. Postgres can do the
        # date_trunc + filter aggregation in one pass.
        date_col = func.date_trunc("day", AgentRun.started_at)
        daily_rows = (
            await session.execute(
                select(
                    date_col.label("day"),
                    AgentRun.kind,
                    func.sum(AgentRun.sales_cost_usd).label("usd"),
                )
                .where(*base_filter)
                .group_by("day", AgentRun.kind)
                .order_by("day")
            )
        ).all()

        # Build a dict of date -> {kind -> credits}. Fill in zeros for
        # missing kinds on each day so the UI's stacked chart doesn't
        # have gaps.
        by_day: dict[str, dict[str, int]] = {}
        for row in daily_rows:
            day = row.day.date().isoformat()
            by_day.setdefault(
                day,
                {"slack_thread": 0, "scheduled_task": 0, "automation": 0, "media": 0},
            )
            by_day[day][row.kind] = _usd_to_credits(row.usd)

        daily_list: list[OverviewDailyBucket] = []
        for day in sorted(by_day.keys()):
            b = by_day[day]
            daily_list.append(
                OverviewDailyBucket(
                    date=day,
                    threads=b.get("slack_thread", 0),
                    scheduled_tasks=b.get("scheduled_task", 0),
                    automations=b.get("automation", 0),
                    media=b.get("media", 0),
                )
            )

        # Category percentages for the chart legend.
        by_kind_rows = (
            await session.execute(
                select(
                    AgentRun.kind,
                    func.sum(AgentRun.sales_cost_usd).label("usd"),
                )
                .where(*base_filter)
                .group_by(AgentRun.kind)
            )
        ).all()
        kind_totals = {r.kind: _usd_to_credits(r.usd) for r in by_kind_rows}
        category_pct: dict[str, float] = {}
        if total_credits > 0:
            for kind_key, label in [
                ("slack_thread", "threads"),
                ("scheduled_task", "scheduled_tasks"),
                ("automation", "automations"),
                ("media", "media"),
            ]:
                category_pct[label] = round(
                    100.0 * kind_totals.get(kind_key, 0) / total_credits, 1
                )

        # Top users (top 5 by credits in window). NULL app_user_id
        # collapses into "System / Misterr".
        top_user_rows = (
            await session.execute(
                select(
                    AgentRun.app_user_id,
                    func.sum(AgentRun.sales_cost_usd).label("usd"),
                )
                .where(*base_filter)
                .group_by(AgentRun.app_user_id)
                .order_by(desc("usd"))
                .limit(5)
            )
        ).all()
        user_ids = [r.app_user_id for r in top_user_rows if r.app_user_id]
        names = await _user_display_names(session, user_ids)
        top_users = [
            OverviewTopUser(
                app_user_id=str(r.app_user_id) if r.app_user_id else None,
                display_name=names.get(r.app_user_id, "Misterr")
                if r.app_user_id
                else "Misterr",
                credits=_usd_to_credits(r.usd),
            )
            for r in top_user_rows
        ]

        # Top scheduled tasks (top 5 by credits). parent_ref_id is
        # nullable; we filter to scheduled_task kind.
        task_filter = list(base_filter) + [
            AgentRun.kind == "scheduled_task",
            AgentRun.parent_ref_id.isnot(None),
        ]
        top_task_rows = (
            await session.execute(
                select(
                    AgentRun.parent_ref_id,
                    AgentRun.parent_name_snapshot,
                    func.sum(AgentRun.sales_cost_usd).label("usd"),
                )
                .where(*task_filter)
                .group_by(AgentRun.parent_ref_id, AgentRun.parent_name_snapshot)
                .order_by(desc("usd"))
                .limit(5)
            )
        ).all()
        top_scheduled_tasks = [
            OverviewTopScheduledTask(
                task_id=str(r.parent_ref_id) if r.parent_ref_id else None,
                name=r.parent_name_snapshot or "(unnamed task)",
                credits=_usd_to_credits(r.usd),
            )
            for r in top_task_rows
        ]

    # Burn rate: average credits per day over the range.
    days_in_range = (
        7 if range == "7d" else 30 if range == "30d" else 90 if range == "90d" else None
    )
    if days_in_range is None:
        # "All time": estimate from first to last activity in window.
        async with get_session() as session:
            extremes = (
                await session.execute(
                    select(
                        func.min(AgentRun.started_at),
                        func.max(AgentRun.started_at),
                    ).where(AgentRun.workspace_id == ws_id)
                )
            ).first()
        if extremes and extremes[0] and extremes[1]:
            span = (extremes[1] - extremes[0]).days or 1
            days_in_range = max(1, span)
        else:
            days_in_range = 1
    burn_per_day = total_credits // max(days_in_range, 1)

    return OverviewResponse(
        total_credits=total_credits,
        burn_per_day=burn_per_day,
        days_in_range=days_in_range,
        daily=daily_list,
        category_pct=category_pct,
        top_users=top_users,
        top_scheduled_tasks=top_scheduled_tasks,
    )


# --------------------------------------------------------------------------- #
# GET /api/usage/team
# --------------------------------------------------------------------------- #


@router.get("/team", response_model=TeamResponse)
async def get_team(
    range: Literal["7d", "30d", "90d", "all"] = "30d",
    user: ResolvedAppUser = Depends(require_app_user),
) -> TeamResponse:
    cutoff = _range_to_cutoff(range)
    ws_id = user.workspace_id

    base_filter = [AgentRun.workspace_id == ws_id]
    if cutoff is not None:
        base_filter.append(AgentRun.started_at >= cutoff)

    async with get_session() as session:
        rows = (
            await session.execute(
                select(
                    AgentRun.app_user_id,
                    func.count()
                    .filter(AgentRun.kind == "slack_thread")
                    .label("threads"),
                    func.count()
                    .filter(AgentRun.kind == "scheduled_task")
                    .label("sched_runs"),
                    func.count()
                    .filter(AgentRun.kind == "automation")
                    .label("auto_runs"),
                    func.sum(AgentRun.sales_cost_usd).label("usd"),
                    func.max(AgentRun.started_at).label("last_activity"),
                )
                .where(*base_filter)
                .group_by(AgentRun.app_user_id)
                .order_by(desc("usd"))
            )
        ).all()

        user_ids = [r.app_user_id for r in rows if r.app_user_id]
        names = await _user_display_names(session, user_ids)

    team_rows = [
        TeamRow(
            app_user_id=str(r.app_user_id) if r.app_user_id else None,
            display_name=names.get(r.app_user_id, "Misterr")
            if r.app_user_id
            else "Misterr",
            threads=int(r.threads or 0),
            scheduled_task_runs=int(r.sched_runs or 0),
            automation_runs=int(r.auto_runs or 0),
            credits=_usd_to_credits(r.usd),
            last_activity=r.last_activity,
        )
        for r in rows
    ]

    return TeamResponse(total_users=len(team_rows), rows=team_rows)


# --------------------------------------------------------------------------- #
# GET /api/usage/activity
# --------------------------------------------------------------------------- #


@router.get("/activity", response_model=ActivityResponse)
async def get_activity(
    range: Literal["7d", "30d", "90d", "all"] = "30d",
    user_id: str | None = None,
    task_id: str | None = None,
    kind: Literal["all", "slack_thread", "scheduled_task", "automation", "media"] = "all",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: ResolvedAppUser = Depends(require_app_user),
) -> ActivityResponse:
    cutoff = _range_to_cutoff(range)
    ws_id = user.workspace_id

    filters = [AgentRun.workspace_id == ws_id]
    if cutoff is not None:
        filters.append(AgentRun.started_at >= cutoff)
    if user_id:
        import uuid as _uuid

        try:
            filters.append(AgentRun.app_user_id == _uuid.UUID(user_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="user_id must be a UUID")
    if task_id:
        import uuid as _uuid

        try:
            filters.append(AgentRun.parent_ref_id == _uuid.UUID(task_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="task_id must be a UUID")
    if kind != "all":
        filters.append(AgentRun.kind == kind)

    async with get_session() as session:
        total_count = (
            await session.execute(
                select(func.count()).select_from(AgentRun).where(*filters)
            )
        ).scalar_one()
        rows = (
            await session.execute(
                select(AgentRun)
                .where(*filters)
                .order_by(desc(AgentRun.started_at))
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
        user_ids = [r.app_user_id for r in rows if r.app_user_id]
        names = await _user_display_names(session, user_ids)

    return ActivityResponse(
        rows=[
            ActivityRow(
                id=str(r.id),
                kind=r.kind,
                parent_name=r.parent_name_snapshot,
                user_display_name=names.get(r.app_user_id, "Misterr")
                if r.app_user_id
                else "Misterr",
                user_id=str(r.app_user_id) if r.app_user_id else None,
                credits=_usd_to_credits(r.sales_cost_usd),
                started_at=r.started_at,
                slack_channel_id=r.slack_channel_id,
                slack_thread_ts=r.slack_thread_ts,
                status=r.status,
            )
            for r in rows
        ],
        page=page,
        page_size=page_size,
        total_count=int(total_count),
    )


# --------------------------------------------------------------------------- #
# GET /api/usage/scheduled-tasks
# --------------------------------------------------------------------------- #


@router.get("/scheduled-tasks", response_model=ScheduledTaskResponse)
async def get_scheduled_tasks(
    range: Literal["7d", "30d", "90d", "all"] = "30d",
    system_only: bool = False,
    user_id: str | None = None,
    user: ResolvedAppUser = Depends(require_app_user),
) -> ScheduledTaskResponse:
    cutoff = _range_to_cutoff(range)
    ws_id = user.workspace_id

    # Aggregate per-task usage from agent_run, then enrich with the
    # task's current `scope` + `created_by_user_id` via a join. Rows
    # where the parent_ref_id no longer exists (task deleted) still
    # appear; UI shows them with the snapshotted name + no settings link.
    run_filter = [
        AgentRun.workspace_id == ws_id,
        AgentRun.kind == "scheduled_task",
        AgentRun.parent_ref_id.isnot(None),
    ]
    if cutoff is not None:
        run_filter.append(AgentRun.started_at >= cutoff)

    async with get_session() as session:
        # Aggregate stats per task.
        agg_rows = (
            await session.execute(
                select(
                    AgentRun.parent_ref_id,
                    AgentRun.parent_name_snapshot,
                    func.count().label("total_runs"),
                    func.max(AgentRun.started_at).label("last_activity"),
                    func.sum(AgentRun.sales_cost_usd).label("usd"),
                )
                .where(*run_filter)
                .group_by(AgentRun.parent_ref_id, AgentRun.parent_name_snapshot)
                .order_by(desc("usd"))
            )
        ).all()

        # Pull task metadata (scope, created_by) in one bulk join.
        task_ids = [r.parent_ref_id for r in agg_rows]
        task_meta: dict[Any, dict] = {}
        if task_ids:
            meta_rows = (
                await session.execute(
                    select(
                        ScheduledTask.id,
                        ScheduledTask.scope,
                        ScheduledTask.created_by_user_id,
                    ).where(ScheduledTask.id.in_(task_ids))
                )
            ).all()
            for mr in meta_rows:
                task_meta[mr.id] = {
                    "scope": mr.scope,
                    "created_by_user_id": mr.created_by_user_id,
                }

        # Resolve display names for creators.
        creator_ids = [
            v["created_by_user_id"]
            for v in task_meta.values()
            if v.get("created_by_user_id")
        ]
        names = await _user_display_names(session, creator_ids)

    out_rows: list[ScheduledTaskRow] = []
    for r in agg_rows:
        meta = task_meta.get(r.parent_ref_id, {})
        is_system = meta.get("scope") == "system"
        if system_only and not is_system:
            continue
        cb = meta.get("created_by_user_id")
        if user_id:
            # Filter to tasks owned by a specific user.
            try:
                import uuid as _uuid

                if cb != _uuid.UUID(user_id):
                    continue
            except ValueError:
                raise HTTPException(
                    status_code=400, detail="user_id must be a UUID"
                )
        out_rows.append(
            ScheduledTaskRow(
                task_id=str(r.parent_ref_id) if r.parent_ref_id else None,
                name=r.parent_name_snapshot or "(unnamed)",
                total_runs=int(r.total_runs or 0),
                last_activity=r.last_activity,
                created_by_user_id=str(cb) if cb else None,
                created_by_display_name=names.get(cb, "Misterr")
                if cb
                else "Misterr",
                total_credits=_usd_to_credits(r.usd),
                is_system=is_system,
            )
        )

    return ScheduledTaskResponse(rows=out_rows)


__all__ = ["router"]
