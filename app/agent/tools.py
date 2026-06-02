"""Generic tool registry + trivial demo tools. No domain logic.

A tool has a name, description, JSON schema, an async handler, and a `risky`
flag. Risky tools require human approval (gate) before running.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import structlog

from app.agent.router import run_cheap
from app.agent.sandbox import run_code as _sandbox_run_code
from app.integrations import gateway as _gateway

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Awaitable[str]]
    risky: bool = False
    # Optional async per-call risk check (tool input -> bool). Overrides `risky`
    # when present; used by run_action to gate writes dynamically (fail-safe).
    risky_check: Callable[[dict], Awaitable[bool]] | None = None


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    _REGISTRY[tool.name] = tool


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def anthropic_tool_specs() -> list[dict[str, Any]]:
    """Tool definitions in the shape the Anthropic Messages API expects."""
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in _REGISTRY.values()
    ]


# --------------------------------------------------------------------------- #
# Demo tools (trivial, generic, no domain). Just to exercise the agent loop.
# --------------------------------------------------------------------------- #

async def _get_current_time() -> str:
    return datetime.now(timezone.utc).isoformat()


_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        return _ALLOWED_BINOPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
        return _ALLOWED_UNARY[type(node.op)](_safe_eval(node.operand))
    raise ValueError("expresión no permitida")


async def _calc(expression: str) -> str:
    result = _safe_eval(ast.parse(expression, mode="eval"))
    return str(result)


async def _delegate_simple(task: str) -> str:
    # Routes the sub-task to the cheap model via LiteLLM (see router.py).
    return await run_cheap(task)


async def _simulate_destructive_action(target: str) -> str:
    # RISKY demo tool: does nothing real, only exists to exercise the approval gate.
    log.info("simulate_destructive_action", target=target)
    return f"(simulado) acción destructiva sobre {target!r} ejecutada. No se tocó nada real."


register(Tool(
    name="get_current_time",
    description="Get the current UTC time as an ISO 8601 string.",
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_get_current_time,
))
register(Tool(
    name="calc",
    description="Evaluate a basic arithmetic expression, e.g. '2*(3+4)'. Supports + - * / % ** and parentheses.",
    input_schema={
        "type": "object",
        "properties": {"expression": {"type": "string", "description": "Arithmetic expression"}},
        "required": ["expression"],
    },
    handler=_calc,
))
register(Tool(
    name="delegate_simple",
    description=(
        "Delegate a self-contained, simple sub-task (e.g. a quick summary or "
        "classification) to a cheaper/faster model and return its text result."
    ),
    input_schema={
        "type": "object",
        "properties": {"task": {"type": "string", "description": "The simple sub-task prompt"}},
        "required": ["task"],
    },
    handler=_delegate_simple,
))
register(Tool(
    name="simulate_destructive_action",
    description=(
        "Simulate a destructive/irreversible action on a target. Demo of a RISKY "
        "tool requiring human approval; it performs no real action."
    ),
    input_schema={
        "type": "object",
        "properties": {"target": {"type": "string", "description": "What would be affected"}},
        "required": ["target"],
    },
    handler=_simulate_destructive_action,
    risky=True,
))


# --------------------------------------------------------------------------- #
# Platform capabilities used by skills (sandbox + skill loading). Generic; a
# skill's SKILL.md tells the agent HOW to use these, but ships no code itself.
# --------------------------------------------------------------------------- #

async def _run_code(code: str) -> str:
    return await _sandbox_run_code(code)


async def _load_skill(name: str) -> str:
    """Per-user skill loader. Resolves the current user from contextvars
    (set by the runner), fetches the body via the registry (which handles R2
    + LRU + cross-reference detection), and returns a model-friendly text
    block. The body is wrapped in `<skill name="...">` so the model treats it
    as content, not instructions."""
    import uuid as _uuid
    from app.agent.context import app_user_id_var, run_id_var, workspace_id_var
    from app.skills import registry as _sk_registry

    user_str = app_user_id_var.get()
    if not user_str:
        return (
            "Error: no hay contexto de usuario, no puedo cargar la skill. "
            "Esto es un bug del runner; reintentá."
        )
    try:
        user_id = _uuid.UUID(user_str)
    except (ValueError, TypeError):
        return "Error: contexto de usuario corrupto."
    thread_id = run_id_var.get()
    loaded = None
    try:
        loaded = await _sk_registry.load_skill_body_for_user(
            user_id, name, thread_id=thread_id
        )
    except _sk_registry.SkillNotFound:
        # Fallback for auto-generated `integrations/<app>` skills + any
        # other workspace-scope `source='catalog'` skill: they're not
        # installed per-user but every user in the workspace should
        # still be able to load them. The previous version returned
        # "no instalada" here, which sent the agent down a broken
        # discovery path (it would try find_actions and fail).
        ws_str = workspace_id_var.get()
        if ws_str:
            try:
                workspace_id = _uuid.UUID(ws_str) if isinstance(ws_str, str) else ws_str
                loaded = await _sk_registry.load_workspace_catalog_skill(
                    workspace_id, name, thread_id=thread_id
                )
            except _sk_registry.SkillNotFound:
                pass
            except _sk_registry.SkillError as exc:
                return f"Error cargando la skill {name!r}: {exc}"
        if loaded is None:
            return (
                f"Skill {name!r} no instalada para este usuario. Pedile al "
                "humano que la instale con `/misterr skill upload` (o que "
                "te diga si la instaló bajo otro nombre)."
            )
    except _sk_registry.SkillError as exc:
        return f"Error cargando la skill {name!r}: {exc}"

    # Escape the name in the wrapper. The body itself is markdown and we trust
    # markdown; what we don't trust is the skill body trying to break out of
    # the wrapper. Names are slug-validated so this is mostly defensive.
    safe_name = name.replace('"', "&quot;").replace("<", "&lt;")
    lines = [
        f'<skill name="{safe_name}">',
        loaded.body.strip(),
        "</skill>",
        "",
        f"Skill `{loaded.name}` cargada. {loaded.description}",
    ]
    if loaded.links:
        installed = [link for link in loaded.links if link not in loaded.missing_links]
        if installed:
            lines.append(
                "Referencias internas instaladas: "
                + ", ".join(f"`{link}`" for link in installed)
                + ". Si necesitás su contenido, cargalas con load_skill."
            )
    if loaded.warning:
        lines.append(loaded.warning)
    return "\n".join(lines)


register(Tool(
    name="run_code",
    description=(
        "Run Python in an isolated per-run sandbox (pandas, numpy, matplotlib "
        "available). To return a downloadable file, save it under "
        "/home/user/outputs/ and a signed link will be returned automatically."
    ),
    input_schema={
        "type": "object",
        "properties": {"code": {"type": "string", "description": "Python source to execute"}},
        "required": ["code"],
    },
    handler=_run_code,
))
register(Tool(
    name="load_skill",
    description=(
        "Load the full instructions (SKILL.md) of an installed skill by name, "
        "when its description matches the task."
    ),
    input_schema={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Skill name"}},
        "required": ["name"],
    },
    handler=_load_skill,
))


# --------------------------------------------------------------------------- #
# Credentialed integrations (Pipedream Connect). Generic: no provider hardcoded.
# Credentials are injected server-side by the gateway; the model never sees them.
# --------------------------------------------------------------------------- #

async def _list_integrations() -> str:
    return await _gateway.list_integrations()


async def _find_actions(app: str, query: str | None = None) -> str:
    return await _gateway.find_actions(app, query)


async def _run_integration_action(
    app: str,
    action_id: str,
    params: dict | None = None,
    filter_substring: str | None = None,
) -> str:
    return await _gateway.run_action(
        app, action_id, params or {}, filter_substring=filter_substring or None,
    )


async def _find_in_action(
    app: str,
    action_id: str,
    filter_substring: str,
    base_params: dict | None = None,
    date_from_param: str = "fromDateTime",
    date_to_param: str = "toDateTime",
    window_days: int = 2,
    max_iterations: int = 15,
    end_iso: str | None = None,
) -> str:
    return await _gateway.find_in_action(
        app=app,
        action_id=action_id,
        filter_substring=filter_substring,
        base_params=base_params or {},
        date_from_param=date_from_param,
        date_to_param=date_to_param,
        window_days=window_days,
        max_iterations=max_iterations,
        end_iso=end_iso,
    )


async def _run_action_gate(inp: dict) -> bool:
    # Fail-safe gate: writes/ambiguous actions require approval (see gateway).
    return await _gateway.should_gate(inp.get("action_id", ""))


register(Tool(
    name="list_integrations",
    description=(
        "List the workspace's connected integrations (apps), each with its "
        "current status and the date it was connected. Use when the user asks "
        "what is connected / 'qué tengo conectado' / 'mis integraciones'."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_list_integrations,
))
register(Tool(
    name="find_actions",
    description=(
        "Discover the available actions of a connected integration app (e.g. "
        "'gitlab', 'slack'). Returns action ids to pass to run_action."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "app": {"type": "string", "description": "Connected app slug"},
            "query": {"type": "string", "description": "Optional search term"},
        },
        "required": ["app"],
    },
    handler=_find_actions,
))
async def _disconnect_integration(app: str) -> str:
    return await _gateway.disconnect_integration(app)


register(Tool(
    name="disconnect_integration",
    description=(
        "Disconnect an integration: removes the connection from this workspace. "
        "Idempotent (no-op if not connected). Use when the user says "
        "'desconecta X / quita X / saca la conexión de X / disconnect X'."
    ),
    input_schema={
        "type": "object",
        "properties": {"app": {"type": "string", "description": "Connected app slug"}},
        "required": ["app"],
    },
    handler=_disconnect_integration,
    risky=True,
))


async def _request_integration_noop(app: str) -> str:
    # The tools node intercepts this tool (it triggers an interrupt + connect flow).
    # This handler is a fallback safety net and should not normally be invoked.
    return f"Solicitada la conexión de {app}; esperando confirmación."


register(Tool(
    name="request_integration",
    description=(
        "Ask the user to connect an integration app that is needed but not yet "
        "connected in this workspace. Use this when list_integrations does not "
        "include the app the task needs. Provide the canonical app slug (e.g. "
        "'notion', 'google_sheets', 'gmail'). Posts a connect link in Slack and "
        "pauses the run; it auto-resumes once the user finishes connecting."
    ),
    input_schema={
        "type": "object",
        "properties": {"app": {"type": "string", "description": "Canonical app slug"}},
        "required": ["app"],
    },
    handler=_request_integration_noop,
))


register(Tool(
    name="run_action",
    description=(
        "Run an action of a connected integration. Credentials are injected "
        "server-side; you never provide or see them. Give the app, the action_id "
        "(from find_actions), and the action's params.\n\n"
        "OPTIONAL: pass `filter_substring` when the user is looking for a "
        "specific entity (company name, person, email, account id) inside a "
        "list-style response. The gateway runs the action, then keeps only "
        "items whose nested data contains the substring (case-insensitive, "
        "any field at any depth: parties[].emailAddress, "
        "context[].objects[].fields[].value, metaData.title, etc.) and drops "
        "the rest BEFORE the LLM sees them. Use this for needle-in-haystack "
        "queries like 'last call with MercadoLibre' to avoid iterating the "
        "same action with different date windows."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "app": {"type": "string", "description": "Connected app slug"},
            "action_id": {"type": "string", "description": "Action id from find_actions"},
            "params": {"type": "object", "description": "Action parameters"},
            "filter_substring": {
                "type": "string",
                "description": (
                    "Optional. Case-insensitive substring that response items "
                    "must contain in any nested field to be kept. Use for "
                    "needle searches (company/person/email). Leave empty for "
                    "the unfiltered response."
                ),
            },
        },
        "required": ["app", "action_id"],
    },
    handler=_run_integration_action,
    risky_check=_run_action_gate,
))


register(Tool(
    name="find_in_action",
    description=(
        "Iteratively sweep backward-walking date windows on a list-style "
        "action until at least one item matches `filter_substring`. Use "
        "for needle-in-haystack searches on actions that DON'T expose a "
        "cursor (e.g. gong-get-extensive-data, hubspot list endpoints, "
        "salesforce list endpoints) when a single `run_action(... "
        "filter_substring=...)` would only see the most recent N rows "
        "and miss the match.\n\n"
        "The gateway sweeps server-side: each iteration spends ~$0.005, "
        "stops as soon as one match shows up, returns the matching rows "
        "with `[find_in_action] hit on iteration K/N (window ...)` "
        "footer so you know how deep we went. Caps: max_iterations=60.\n\n"
        "Read the action's date param names off `load_skill` / "
        "`find_actions` and pass them as `date_from_param` / "
        "`date_to_param`. Defaults match Gong; HubSpot may need "
        "`createdAfter`/`createdBefore`, Salesforce `start`/`end`, etc.\n\n"
        "Prefer `run_action(... filter_substring=...)` for a single "
        "window. Reach for `find_in_action` only when you have to walk "
        "history (e.g. the answer might be older than the action's "
        "default page) and the action has no cursor."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "app": {"type": "string", "description": "Connected app slug"},
            "action_id": {
                "type": "string",
                "description": "Action id from find_actions (must be list-style with date filters)",
            },
            "filter_substring": {
                "type": "string",
                "description": (
                    "Case-insensitive substring the gateway will deep-match "
                    "against each response item's nested fields. The needle "
                    "(company name, email, account id, person name)."
                ),
            },
            "base_params": {
                "type": "object",
                "description": (
                    "Non-date params to merge into every sweep call (e.g. "
                    "`context='Extended'`, `includeParties=true`, "
                    "`maxResults=100`). Date params, if you include them "
                    "here, are stripped — find_in_action owns the window."
                ),
            },
            "date_from_param": {
                "type": "string",
                "description": (
                    "Name of the 'start of window' date param on this "
                    "action. Default 'fromDateTime' (Gong). Other examples: "
                    "'createdAfter' (HubSpot), 'start' (Salesforce)."
                ),
            },
            "date_to_param": {
                "type": "string",
                "description": (
                    "Name of the 'end of window' date param. Default "
                    "'toDateTime' (Gong)."
                ),
            },
            "window_days": {
                "type": "integer",
                "description": (
                    "Size of each window in days. Default 2. Use smaller "
                    "(1) for high-traffic data, larger (7) for sparse."
                ),
            },
            "max_iterations": {
                "type": "integer",
                "description": (
                    "Max number of windows to sweep. Default 15. Hard cap 60."
                ),
            },
            "end_iso": {
                "type": "string",
                "description": (
                    "Optional ISO-8601 datetime to anchor the most recent "
                    "window. Defaults to now (UTC). Use e.g. "
                    "'2026-06-02T23:59:59Z'."
                ),
            },
        },
        "required": ["app", "action_id", "filter_substring"],
    },
    handler=_find_in_action,
    risky_check=_run_action_gate,
))


# --------------------------------------------------------------------------- #
# Spaces: live, isolated read-only dashboards backed by the integrations
# gateway (slice 4a). Single fixed template parametrized by config; the
# agent provides config, NEVER generates code.
# --------------------------------------------------------------------------- #

from app.spaces import gateway as _spaces


# --------------------------------------------------------------------------- #
# Datadog: HTTP-direct query tool. Covers /api/v1/query (the endpoint that
# powers every Datadog dashboard widget). For when the Pipedream connector
# doesn't expose the action you need -- which is most of APM. Read-only.
# --------------------------------------------------------------------------- #

async def _datadog_query(
    query: str, from_seconds_ago: int = 3600, to_seconds_ago: int = 0
) -> str:
    from app.integrations.datadog import DatadogError, format_query_result, query_metrics
    try:
        result = await query_metrics(query, from_seconds_ago, to_seconds_ago)
    except DatadogError as exc:
        return f"Error de Datadog: {exc}"
    return format_query_result(result, query)


register(Tool(
    name="datadog_query",
    description=(
        "Run a Datadog metric query and return the result. Uses Datadog's "
        "/api/v1/query endpoint -- the same one every dashboard widget uses, "
        "so anything visible in a Datadog dashboard is queryable here. "
        "Examples of useful queries:\n"
        "  - `sum:users.unique{*}` -- total unique users.\n"
        "  - `avg:trace.web.request.duration{*} by {service}` -- avg latency per service.\n"
        "  - `top(sum:trace.web.request.errors{*} by {resource_name}, 10, 'sum', 'desc')` "
        "-- top 10 resources by errors (APM).\n"
        "  - `sum:datadog.estimated_usage.logs.events{*}.as_count()` -- log volume.\n"
        "The query is the same syntax used in Datadog dashboards. When the "
        "user asks 'what does this widget show', either ask them for the "
        "metric/query of the widget or compose a sensible query yourself. "
        "Time window: from_seconds_ago=3600 means last hour (default), "
        "to_seconds_ago=0 means up to now (default). Read-only, no approval gate."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Datadog query string (same syntax as a dashboard widget query).",
            },
            "from_seconds_ago": {
                "type": "integer",
                "description": "Start of time window, seconds ago. Default 3600 (1h).",
            },
            "to_seconds_ago": {
                "type": "integer",
                "description": "End of time window, seconds ago. Default 0 (now).",
            },
        },
        "required": ["query"],
    },
    handler=_datadog_query,
))


# --------------------------------------------------------------------------- #
# Slack mentions: roster lookup. Used by the agent when it wants to mention a
# user but doesn't have their Slack id. Read-only, no gate.
# --------------------------------------------------------------------------- #

async def _find_slack_user(query: str) -> str:
    from app.agent.context import workspace_id_var
    from app.slack import roster as _roster
    ws_str = workspace_id_var.get()
    if not ws_str:
        return "Error: sin contexto de workspace."
    import uuid as _uuid
    ws = _uuid.UUID(ws_str)
    candidates = await _roster.find_user(ws, query)
    # Apps/bots are valid mention targets (the <@U...> syntax triggers them
    # like humans). We surface `is_bot` in the output so the model can warn
    # the user if it's about to ping another bot.
    if not candidates:
        return (
            f"No encontré ningún usuario para `{query}` en este workspace. "
            "Pedile al humano que confirme el nombre exacto o su handle de Slack."
        )
    if len(candidates) == 1:
        c = candidates[0]
        tag = " [app/bot]" if c.get("is_bot") else ""
        return (
            f"Found: <@{c['slack_user_id']}>{tag} "
            f"(display: {c.get('display_name') or '?'}, real: {c.get('real_name') or '?'}). "
            f"Usá `<@{c['slack_user_id']}>` cuando quieras mencionarlo."
        )
    lines = [
        f"• {c.get('display_name') or '?'} / {c.get('real_name') or '?'}"
        f"{' [app/bot]' if c.get('is_bot') else ''} — <@{c['slack_user_id']}>"
        for c in candidates[:10]
    ]
    extra = f"\n(+{len(candidates)-10} más)" if len(candidates) > 10 else ""
    return (
        f"Ambiguous: {len(candidates)} matches for `{query}`. Pedile al humano "
        f"cuál de estos quería:\n" + "\n".join(lines) + extra
    )


register(Tool(
    name="find_slack_user",
    description=(
        "Find a Slack user in the current workspace by display name, real name, "
        "or email. Returns the canonical Slack user id ready to use as <@U...> "
        "for a mention. If there's ambiguity (e.g. two users named Sam), "
        "returns the candidates so you can ask the human to disambiguate. "
        "Call this BEFORE writing `@name` plain in a message -- Slack only "
        "renders + notifies for the <@U...> form. Never invent a user id."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Name, partial name, or email (or its prefix) of the Slack user.",
            },
        },
        "required": ["query"],
    },
    handler=_find_slack_user,
))


async def _deploy_space(name: str, data_binding: dict, access_list: list | None = None) -> str:
    return await _spaces.deploy_space(name, data_binding, access_list or [])


async def _list_spaces() -> str:
    return await _spaces.list_spaces()


async def _delete_space(space_id: str) -> str:
    return await _spaces.delete_space(space_id)


async def _update_space_access(space_id: str, access_list: list) -> str:
    return await _spaces.update_space_access(space_id, access_list)


async def _update_space_binding(space_id: str, data_binding: dict) -> str:
    return await _spaces.update_space_binding(space_id, data_binding)


register(Tool(
    name="deploy_space",
    description=(
        "Deploy a Space: a live, isolated read-only dashboard for this "
        "workspace, backed by the integrations gateway. `data_binding` "
        "describes WHAT to query (must include `app` and `action_id`, "
        "optionally `params` and `refresh_interval` in seconds). `access_list` "
        "is the list of users authorized to view it. The Space has its own URL "
        "and is logically isolated from other Spaces. Single fixed template; "
        "do NOT generate code. Use list_integrations + find_actions FIRST to "
        "pick the right app/action and discover params."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Human-friendly Space name."},
            "data_binding": {
                "type": "object",
                "description": "What the Space queries via the gateway. Keys: app, action_id, params (object), refresh_interval (seconds).",
            },
            "access_list": {
                "type": "array",
                "items": {"type": "object"},
                "description": "List of {user_id|email, role} entries authorized to view the Space.",
            },
        },
        "required": ["name", "data_binding"],
    },
    handler=_deploy_space,
    # Reversible: a created Space can be deleted with `delete_space`. No gate.
))

register(Tool(
    name="list_spaces",
    description="List the Spaces deployed in this workspace (status, URL, access count).",
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_list_spaces,
))

register(Tool(
    name="delete_space",
    description=(
        "Permanently delete a Space (data + URL). Idempotent: missing space is a no-op."
    ),
    input_schema={
        "type": "object",
        "properties": {"space_id": {"type": "string", "description": "Space UUID."}},
        "required": ["space_id"],
    },
    handler=_delete_space,
    risky=True,
))

register(Tool(
    name="update_space_access",
    description="Replace who can view a Space. Does NOT re-provision.",
    input_schema={
        "type": "object",
        "properties": {
            "space_id": {"type": "string"},
            "access_list": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["space_id", "access_list"],
    },
    handler=_update_space_access,
    # Reversible: another update_space_access call restores the previous list.
))

register(Tool(
    name="update_space_binding",
    description="Change what a Space queries (data_binding). Does NOT re-provision.",
    input_schema={
        "type": "object",
        "properties": {
            "space_id": {"type": "string"},
            "data_binding": {"type": "object"},
        },
        "required": ["space_id", "data_binding"],
    },
    handler=_update_space_binding,
    # Reversible: another update_space_binding call restores the previous query.
))


# --------------------------------------------------------------------------- #
# Natural-language skill management. These tools exist so the agent can handle
# "lista mis skills", "instalá X", "desinstalá Y" without depending on Slack
# slash commands. Slash commands are fragile across workspaces (manifest
# propagation, OAuth lifecycle, etc.); the LLM-driven path is robust because
# it only needs DM message delivery, which works wherever the bot is installed.
# --------------------------------------------------------------------------- #


def _current_user_id():
    """Resolve the current AppUser.id (UUID) from contextvars. Returns None if
    no user context is set (shouldn't happen during a real run, but defensive
    so tools don't crash on missing context)."""
    import uuid as _uuid
    from app.agent.context import app_user_id_var
    s = app_user_id_var.get()
    if not s:
        return None
    try:
        return _uuid.UUID(s)
    except (ValueError, TypeError):
        return None


def _current_workspace_id():
    """Resolve the current workspace_id (UUID) from contextvars."""
    import uuid as _uuid
    from app.agent.context import workspace_id_var
    s = workspace_id_var.get()
    if not s:
        return None
    if isinstance(s, _uuid.UUID):
        return s
    try:
        return _uuid.UUID(str(s))
    except (ValueError, TypeError):
        return None


async def _list_my_skills() -> str:
    """List the skills installed for the current user. Returns a human-readable
    block with name, description, and activation. Used when the user says
    'lista mis skills', 'qué skills tengo', etc."""
    from app.skills import registry as _sk
    user_id = _current_user_id()
    if user_id is None:
        return "Error: no hay contexto de usuario."
    installs = await _sk.list_for_user(user_id)
    if not installs:
        return (
            "No tenés skills instaladas todavía. Subí una mandándome un archivo "
            "`.md` en este DM y decime 'instalala como skill'."
        )
    lines = [f"Tenés *{len(installs)}* skills instaladas:"]
    for i in installs:
        s = i.skill
        lines.append(
            f"• `{s.name}` [{i.effective_activation}] — {s.description[:120]}"
        )
    return "\n".join(lines)


async def _list_workspace_skills() -> str:
    """List ALL skills in the workspace, marking which the current user has
    installed vs not. Useful when the user wants to discover what's available
    to install."""
    from app.skills import registry as _sk
    user_id = _current_user_id()
    workspace_id = _current_workspace_id()
    if user_id is None or workspace_id is None:
        return "Error: no hay contexto de usuario/workspace."
    all_skills = await _sk.list_for_workspace(workspace_id)
    if not all_skills:
        return "No hay skills en este workspace todavía."
    user_installs = await _sk.list_for_user(user_id)
    installed_names = {i.skill.name for i in user_installs}
    lines = [f"Skills del workspace ({len(all_skills)} total):"]
    for s in all_skills:
        marker = "✓ instalada" if s.name in installed_names else "○ disponible"
        lines.append(f"• `{s.name}` [{marker}] — {s.description[:120]}")
    lines.append(
        "\nPara instalar una que no tengas, decime 'instalá `nombre`'."
    )
    return "\n".join(lines)


async def _install_skill_by_name(name: str) -> str:
    """Install an existing workspace skill for the current user. The skill
    must already exist in the workspace (it doesn't create new ones; for that
    use upload_skill_from_attachment)."""
    from app.skills import registry as _sk
    user_id = _current_user_id()
    workspace_id = _current_workspace_id()
    if user_id is None or workspace_id is None:
        return "Error: no hay contexto de usuario/workspace."
    skill = await _sk.get_skill_in_workspace(workspace_id, name)
    if skill is None:
        # Be helpful: list what IS available so the LLM can suggest a close match
        # or the user can pick.
        available = await _sk.list_for_workspace(workspace_id)
        names = ", ".join(f"`{s.name}`" for s in available[:10])
        suffix = f" Tenemos: {names}." if names else ""
        return (
            f"No encuentro una skill llamada `{name}` en este workspace.{suffix}"
        )
    await _sk.install_for_user(user_id=user_id, skill_id=skill.id)
    return (
        f"✓ Instalé `{skill.name}` para vos. "
        f"Activation: `{skill.activation_default}`. "
        f"{skill.description[:200]}"
    )


async def _uninstall_skill_by_name(name: str) -> str:
    """Remove a skill from the current user's installs (does NOT delete the
    workspace skill; other users keep it). Idempotent."""
    from app.skills import registry as _sk
    user_id = _current_user_id()
    if user_id is None:
        return "Error: no hay contexto de usuario."
    install = await _sk.get_skill_for_user(user_id, name)
    if install is None:
        return f"No tenés `{name}` instalada, no hay nada para remover."
    await _sk.uninstall_for_user(user_id=user_id, skill_id=install.skill.id)
    return f"✓ Removí `{name}` de tus skills. Podés reinstalarla cuando quieras."


register(Tool(
    name="list_my_skills",
    description=(
        "List the skills currently installed for THIS user. Use when the user "
        "says 'lista mis skills', 'qué skills tengo', 'what skills do I have', "
        "'cuáles skills uso', etc."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_list_my_skills,
))
register(Tool(
    name="list_workspace_skills",
    description=(
        "List ALL skills available in the workspace, marking which ones the "
        "current user has installed vs not. Use when the user asks 'qué skills "
        "hay', 'qué puedo instalar', 'cuáles están disponibles', 'browse skills'."
    ),
    input_schema={"type": "object", "properties": {}, "required": []},
    handler=_list_workspace_skills,
))
register(Tool(
    name="install_skill",
    description=(
        "Install an EXISTING workspace skill for the current user by name. "
        "Does NOT create a new skill from a file; for that the user must "
        "send a .md attachment in DM. Use when the user says 'instalá X', "
        "'install X skill', 'agregame Y'."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Exact skill name (kebab-case slug)",
            },
        },
        "required": ["name"],
    },
    handler=_install_skill_by_name,
))
register(Tool(
    name="uninstall_skill",
    description=(
        "Remove a skill from the current user's installs. Does NOT delete the "
        "skill from the workspace. Use when the user says 'desinstalá X', "
        "'sacame Y', 'remove X skill', 'quitamela'."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill name to uninstall"},
        },
        "required": ["name"],
    },
    handler=_uninstall_skill_by_name,
))


# --------------------------------------------------------------------------- #
# Scheduled tasks (slice T-1). Imported for side effects so the six
# `create/list/update/delete/pause/resume_scheduled_task` tools register at
# module load. Domain logic lives in `app.scheduled_tasks.*`; this just wires
# the tool entries into the global registry.
# --------------------------------------------------------------------------- #
from app.scheduled_tasks import agent_tools as _scheduled_task_tools  # noqa: E402, F401


# --------------------------------------------------------------------------- #
# Persistent workspace memory (slice T-X Phase A). Imported for side effects
# so the `remember` tool registers at module load. The memory module proper
# lives in `app.memory`; this just wires the agent tool entry.
# --------------------------------------------------------------------------- #
from app.agent import memory_tools as _memory_tools  # noqa: E402, F401


# --------------------------------------------------------------------------- #
# Automations (event-driven hooks). Imported for side effects so the six
# create/list/update/delete/pause/resume_automation tools register at module
# load. Domain logic lives in `app.automations.*`; this just wires the tool
# entries into the global registry.
# --------------------------------------------------------------------------- #
from app.automations import agent_tools as _automation_tools  # noqa: E402, F401


# --------------------------------------------------------------------------- #
# Native Slack tools (read_slack_channel / _thread / list / find). Misterr is
# a Slack app and the bot OAuth grants `channels:history` etc. natively, so
# reading Slack history should go through the bot token, NOT through a
# Composio/Pipedream "Slack integration" that nobody installed. Without these
# tools the agent hallucinates a need for that integration and refuses tasks
# like "summarize today's channel highlights" -- the bug from PR #108.
# --------------------------------------------------------------------------- #
from app.slack import agent_tools as _slack_agent_tools  # noqa: E402, F401


# --------------------------------------------------------------------------- #
# Follow-ups: state-driven reactivation nudges. The `schedule_follow_up` tool
# lets the agent agenda a ping for the current thread when it's waiting on
# user input. The worker (lifespan task in app/main.py) fires it after
# `wait_hours` unless the user replied first.
# --------------------------------------------------------------------------- #
from app.agent import follow_up_tools as _follow_up_tools  # noqa: E402, F401
