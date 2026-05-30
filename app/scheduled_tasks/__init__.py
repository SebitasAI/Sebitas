"""Scheduled Tasks (slice T-1).

Tasks created by users via the agent (`create_scheduled_task` tool) that the
backend fires on a cron schedule. Each fire opens a fresh thread, injects the
task's prompt as the seed user message plus a synthetic [Scheduled task
context] system block (last run summary, etc.), and runs the agent.

Two tasks are seeded per workspace (workflow-discovery + daily-brief). The
seeder is idempotent (ON CONFLICT DO NOTHING) and runs on workspace install
and on app startup.

Layout:
- `system_defaults.py` -- hard-coded prompts / cron / tz for the two system
  tasks. Not configurable; user can only pause or change destination.
- `timezone.py` -- IANA name + Spanish-friendly aliases ("hora Col" ->
  America/Bogota), with a Slack-tz fallback.
- `repository.py` -- CRUD + permission checks + workspace-scoped resolver.
- `agent_tools.py` -- the six agent tools (create / list / update / delete /
  pause / resume). Imported for side-effects from `app/agent/tools.py`.
- `scheduler.py` -- background loop that scans `next_run_at <= now()` under
  `FOR UPDATE SKIP LOCKED`, fires the agent, advances next_run_at.
"""
