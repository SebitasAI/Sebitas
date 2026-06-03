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
# Pre-rendered identity block for the calling user (Slack U-id, display name,
# email, timezone). The agent uses this to know who it is talking to without
# having to ask, and to default "tu DM / mandame por DM" to the right id.
# Set by the runner at the start of each turn; empty if lookup failed.
calling_user_identity_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "calling_user_identity", default=""
)
# The Slack channel + conversation key + reply_thread_ts for the active
# turn. Tools that need to schedule something tied to THIS conversation
# (e.g. follow-up nudges that must post back in this thread) read these
# instead of asking the model to thread them through.
calling_channel_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "calling_channel", default=""
)
calling_conversation_key_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "calling_conversation_key", default=""
)
calling_reply_thread_ts_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "calling_reply_thread_ts", default=""
)
# Per-run override of `Settings.agent_max_iterations`. The runner picks
# the seed text apart: long / project-style prompts (Tier 3 of the
# iter-cap protection) get a higher ceiling so genuinely large workflows
# can complete instead of dying mid-tool_use. A value of 0 means
# "no override, use the settings default". `set_run_context` resets it
# between runs so a previous project-mode bump can't leak.
agent_max_iter_var: contextvars.ContextVar[int] = contextvars.ContextVar(
    "agent_max_iter", default=0
)


def set_run_context(
    *,
    workspace_id: str,
    run_id: str,
    skills_context: str,
    channel_roster: str = "",
    app_user_id: str | None = None,
    calling_user_identity: str = "",
    calling_channel: str = "",
    calling_conversation_key: str = "",
    calling_reply_thread_ts: str = "",
    agent_max_iter_override: int = 0,
) -> None:
    workspace_id_var.set(workspace_id)
    app_user_id_var.set(app_user_id)
    run_id_var.set(run_id)
    skills_context_var.set(skills_context)
    channel_roster_var.set(channel_roster)
    calling_user_identity_var.set(calling_user_identity)
    calling_channel_var.set(calling_channel)
    calling_conversation_key_var.set(calling_conversation_key)
    calling_reply_thread_ts_var.set(calling_reply_thread_ts)
    agent_max_iter_var.set(agent_max_iter_override)
