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
run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "run_id", default=None
)
# Compact list of installed-skill descriptions for the current workspace.
skills_context_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "skills_context", default=""
)


def set_run_context(*, workspace_id: str, run_id: str, skills_context: str) -> None:
    workspace_id_var.set(workspace_id)
    run_id_var.set(run_id)
    skills_context_var.set(skills_context)
