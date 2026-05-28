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
from app.skills.registry import load_skill_md as _load_skill_md

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
    return await _load_skill_md(name)


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


async def _run_integration_action(app: str, action_id: str, params: dict | None = None) -> str:
    return await _gateway.run_action(app, action_id, params or {})


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
        "Disconnect an integration: deletes the connected account at the provider "
        "(via Pipedream) and marks it as disconnected for this workspace. "
        "Idempotent (no-op if not connected). Use when the user says "
        "'desconectá X / quitá X / sacá la conexión de X / disconnect X'."
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
        "(from find_actions), and the action's params."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "app": {"type": "string", "description": "Connected app slug"},
            "action_id": {"type": "string", "description": "Action id from find_actions"},
            "params": {"type": "object", "description": "Action parameters"},
        },
        "required": ["app", "action_id"],
    },
    handler=_run_integration_action,
    risky_check=_run_action_gate,
))
