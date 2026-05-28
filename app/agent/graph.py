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
from typing import Annotated, Any, TypedDict

import structlog
from langfuse import get_client
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.claude import call_claude
from app.agent.tools import get_tool
from app.config import get_settings

log = structlog.get_logger(__name__)
_langfuse = get_client()


class AgentState(TypedDict):
    messages: Annotated[list[dict], operator.add]
    iterations: int


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
        try:
            with _langfuse.start_as_current_observation(
                as_type="span", name=f"tool:{tool.name}", input=tu["input"]
            ) as span:
                result = await tool.handler(**tu["input"])
                span.update(output=result)
            return {**block, "content": result}
        except Exception as exc:  # noqa: BLE001
            log.warning("tool_failed", tool=tu["name"], error=str(exc))
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
