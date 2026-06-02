"""LangGraph agent loop.

agent  -> calls Claude (Opus) with tools (adaptive thinking + prompt caching)
tools  -> executes the requested tools concurrently; risky tools pause the graph
          via interrupt() for human approval, then resume.
The loop runs agent -> tools -> agent ... until Claude stops requesting tools.
State is checkpointed in Postgres (see main.py / runner.py), which is what makes
the interrupt/resume (approval gate) survive across the async wait.
"""

from __future__ import annotations

import asyncio
import operator
import uuid
from typing import Annotated, Any, TypedDict

import structlog
from langfuse import get_client, propagate_attributes
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.claude import call_claude
from app.agent.context import workspace_id_var
from app.agent.tools import get_tool
from app.config import get_settings
from app.integrations import gateway

log = structlog.get_logger(__name__)
_langfuse = get_client()


class AgentState(TypedDict):
    messages: Annotated[list[dict], operator.add]
    iterations: int


async def _has_recent_pending_connect(workspace_id: uuid.UUID, app: str) -> bool:
    """Has there been a connect request for (workspace, app) recently enough
    that a button is likely still actionable? Avoids posting a duplicate link.

    Local import to keep the module load light and avoid an import cycle."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.db.models import IntegrationConnection
    from app.db.session import get_session

    # Postgres `created_at` columns are TIMESTAMP WITHOUT TIME ZONE (naive UTC
    # under the hood — see TimestampMixin). asyncpg rejects binding a tz-aware
    # datetime against a naive column, so strip tzinfo to match.
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None)
    async with get_session() as session:
        row = (
            await session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.app == app,
                    IntegrationConnection.status == "pending",
                    IntegrationConnection.created_at > cutoff,
                )
            )
        ).scalar_one_or_none()
    return row is not None


def _tool_use_blocks(message: dict) -> list[dict]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]


async def _agent_node(state: AgentState) -> dict:
    response = await call_claude(state["messages"])
    # Store as plain dicts so the checkpointer can serialize them, and so thinking
    # block signatures are preserved across turns (required for tool use).
    content = [block.model_dump() for block in response.content]
    return {
        "messages": [{"role": "assistant", "content": content}],
        "iterations": state.get("iterations", 0) + 1,
    }


def _route_after_agent(state: AgentState):
    last = state["messages"][-1]
    if last.get("role") == "assistant" and _tool_use_blocks(last):
        if state.get("iterations", 0) < get_settings().agent_max_iterations:
            return "tools"
    return END


async def _tools_node(state: AgentState) -> dict:
    tool_uses = _tool_use_blocks(state["messages"][-1])

    # In-conversation connect flow: if the agent calls request_integration,
    # handle it FIRST. Idempotency: if already connected, return a tool_result
    # nudging it to retry. Otherwise interrupt + checkpoint; the runner will
    # post the connect button and start the polling fallback. On resume (from
    # webhook or poll), interrupt() returns and we yield the "connected" result.
    connect_calls = [tu for tu in tool_uses if tu["name"] == "request_integration"]
    if connect_calls:
        tu = connect_calls[0]
        app = (tu.get("input") or {}).get("app", "")
        ws_str = workspace_id_var.get()
        ws_uuid = uuid.UUID(ws_str) if ws_str else None
        if ws_uuid and app and await gateway.is_connected(ws_uuid, app):
            content = f"La integración {app!r} ya está conectada en este workspace. Reintentá la action."
        elif ws_uuid and app and await _has_recent_pending_connect(ws_uuid, app):
            # Idempotency: a pending connect already has a button posted; don't
            # duplicate it. The user should complete that one (or wait for it
            # to time out). The runner won't post another link.
            content = (
                f"Ya hay una solicitud de conexión abierta para {app!r}. "
                "El usuario tiene que completarla (o esperar a que expire) "
                "antes de reintentar."
            )
        else:
            interrupt({"type": "connect", "app": app})
            content = f"Integración {app!r} conectada. Reintentá la action."
        return {"messages": [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tu["id"], "content": content}
        ]}]}

    # Per-call risk: a tool may decide riskiness dynamically (risky_check) — e.g.
    # run_action gating writes/ambiguous actions — otherwise its static `risky`.
    risk: dict[str, bool] = {}
    for tu in tool_uses:
        tool = get_tool(tu["name"])
        if tool is None:
            risk[tu["id"]] = False
        elif tool.risky_check is not None:
            risk[tu["id"]] = await tool.risky_check(tu["input"])
        else:
            risk[tu["id"]] = tool.risky

    # Human-in-the-loop gate: pause for approval if any risky tool is requested.
    # interrupt() suspends the graph (checkpointed) and returns the resume value.
    risky = [tu for tu in tool_uses if risk[tu["id"]]]
    decision = "approve"
    if risky:
        decision = interrupt(
            {
                "type": "approval",
                "tools": [
                    {"id": tu["id"], "name": tu["name"], "input": tu["input"]}
                    for tu in risky
                ],
            }
        )

    async def _run(tu: dict) -> dict:
        tool = get_tool(tu["name"])
        block = {"type": "tool_result", "tool_use_id": tu["id"]}
        if tool is None:
            return {**block, "content": f"Error: tool desconocida {tu['name']!r}", "is_error": True}
        if risk[tu["id"]] and decision != "approve":
            return {**block, "content": "Rechazado por el usuario; no se ejecutó."}
        # Per-tool Langfuse tags. We add a coarse `tool:<name>` plus,
        # where it makes sense, a more specific `app:<app>` (run_action)
        # or `skill:<name>` (load_skill) so the Langfuse UI can answer
        # "show me every trace that touched Metabase" or "every trace
        # where the datalake-guide skill was loaded" with a single
        # filter.
        #
        # v3 SDK doesn't have `update_current_trace(tags=...)` anymore
        # (it was removed and was generating "Langfuse object has no
        # attribute update_current_trace" warnings on every run). The
        # supported primitive is `propagate_attributes(tags=...)` as a
        # context manager -- it sets tags on the active span AND any
        # new spans created inside the block, and OpenTelemetry rolls
        # those up to the root trace for UI-level filtering.
        extra_tags = [f"tool:{tool.name}"]
        inp = tu.get("input") or {}
        if tool.name == "run_action" and inp.get("app"):
            extra_tags.append(f"app:{inp['app']}")
        elif tool.name == "load_skill" and inp.get("name"):
            extra_tags.append(f"skill:{inp['name']}")
        elif tool.name in ("install_skill", "uninstall_skill") and inp.get("name"):
            extra_tags.append(f"skill:{inp['name']}")
        elif tool.name == "request_integration" and inp.get("app"):
            extra_tags.append(f"app:{inp['app']}")

        try:
            with propagate_attributes(tags=extra_tags):
                with _langfuse.start_as_current_observation(
                    as_type="span", name=f"tool:{tool.name}", input=tu["input"]
                ) as span:
                    result = await tool.handler(**tu["input"])
                    span.update(output=result)
            return {**block, "content": result}
        except Exception as exc:  # noqa: BLE001
            log.warning("tool_failed", tool=tu["name"], error=str(exc))
            # Surface the failure in the Langfuse UI. v3 way: set the
            # current span's level to ERROR + add a `tool_failed:<name>`
            # tag via propagate_attributes scoped to the failure-handling
            # block. The span at this point is the agent loop's parent
            # (the tool span already exited on the exception).
            try:
                with propagate_attributes(tags=[f"tool_failed:{tool.name}"]):
                    _langfuse.update_current_span(
                        level="ERROR",
                        status_message=str(exc)[:200],
                    )
            except Exception:  # noqa: BLE001
                pass
            return {**block, "content": f"Error ejecutando {tu['name']}: {exc}", "is_error": True}

    # Independent tool calls in a turn run concurrently.
    results = await asyncio.gather(*[_run(tu) for tu in tool_uses])
    return {"messages": [{"role": "user", "content": list(results)}]}


def build_graph(checkpointer: Any):
    graph = StateGraph(AgentState)
    graph.add_node("agent", _agent_node)
    graph.add_node("tools", _tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", _route_after_agent)
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)


# Compiled graph holder, set at app startup (after the checkpointer is ready).
_GRAPH: Any = None


def set_graph(graph: Any) -> None:
    global _GRAPH
    _GRAPH = graph


def get_graph() -> Any:
    if _GRAPH is None:
        raise RuntimeError("agent graph not initialized")
    return _GRAPH
