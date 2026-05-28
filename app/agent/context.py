"""Per-run tenancy context, propagated via contextvars so generic tools
(run_code, load_skill) can scope to the right workspace/run without changing the
tool interface. Set at the start of each run (run_agent / resume_run); read by
the sandbox, the skill loader, and the model call (for the installed-skills list).
contextvars are async-safe and copied into the graph/gather tasks of the run.
"""

from __future__ import annotations

import contextvars

workspace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "workspace_id", default=None
)
# Per-user identity inside the workspace, used by per-user features (Skills:
# install list, load_skill scoping). Resolved by the runner from slack_user_id
# at the start of each run; None until set.
app_user_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "app_user_id", default=None
)
run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "run_id", default=None
)
# Pre-built system-prompt fragment with the skills the current user has
# installed (always-active bodies + on-demand descriptions). The runner
# computes this once per turn so the model call doesn't re-query.
skills_context_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "skills_context", default=""
)
# Compact list of channel members for the current run (id + display name),
# fed into Claude as an uncached system block so the model can mention without
# a tool round-trip. Empty when the run is in a DM or roster sync failed.
channel_roster_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "channel_roster", default=""
)


def set_run_context(
    *,
    workspace_id: str,
    run_id: str,
    skills_context: str,
    channel_roster: str = "",
    app_user_id: str | None = None,
) -> None:
    workspace_id_var.set(workspace_id)
    app_user_id_var.set(app_user_id)
    run_id_var.set(run_id)
    skills_context_var.set(skills_context)
    channel_roster_var.set(channel_roster)
