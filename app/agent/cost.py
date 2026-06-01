"""LLM cost accounting for Langfuse scoring.

We don't lean on Langfuse's server-side cost calculation (it requires a
post-trace API round-trip + race-window with their pipeline). Instead we
sum usage locally as each Claude/LiteLLM call returns, compute USD with a
small in-tree pricing table, and emit two scores at the end of an
agent run:

    - `total_cost_usd`  -- raw model cost across the whole run.
    - `sales_cost_usd`  -- total_cost * SALES_COST_MULTIPLIER.

The multiplier covers (a) fixed infra costs amortized per run
(Neon + Render), (b) blended margin, (c) misc overhead. Today everything
non-LLM is on a free tier so the LLM is the only real variable cost --
when that stops being true (Composio paid plan etc.), we'd want a
per-tool cost hook (see `app/integrations/`). For now LLM-only is a
good proxy.

Anthropic published list pricing is the source of truth. Keep this
table updated when models change tier.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Final

import structlog

log = structlog.get_logger(__name__)


# Anthropic list pricing per 1M tokens. Update on tier change.
# (input, output) -- both in USD per 1M tokens.
_PRICING_PER_MTOK: Final[dict[str, tuple[float, float]]] = {
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    # LiteLLM provider/model form used by the cheap router. Same pricing.
    "anthropic/claude-haiku-4-5": (1.00, 5.00),
}

# Markup applied to total LLM cost to arrive at sales/blended cost. See
# module docstring for the rationale; tweak as the cost mix changes.
SALES_COST_MULTIPLIER: Final[float] = 4.0


@dataclass
class _Accumulator:
    """Per-run usage accumulator. One instance is created at the start of
    every agent run and replaced on the next. Aggregates across all model
    calls inside the run (main Opus turns + cheap-model delegations)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    usd_cost: float = 0.0
    by_model: dict[str, dict[str, float | int]] = field(default_factory=dict)


# contextvar so concurrent agent runs in the same process don't trample
# each other's accumulators. `runner.run_agent` resets it on each turn.
_usage_var: contextvars.ContextVar[_Accumulator | None] = contextvars.ContextVar(
    "agent_cost_usage", default=None
)


def start_run_accumulator() -> None:
    """Initialise (or reset) the accumulator for the current asyncio task /
    Slack turn. Idempotent: calling twice replaces."""
    _usage_var.set(_Accumulator())


def add_usage(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> None:
    """Called by each LLM-call wrapper after the model returns. Cheap if
    the accumulator isn't set (e.g. during startup tasks); skips silently."""
    acc = _usage_var.get()
    if acc is None:
        return
    pricing = _PRICING_PER_MTOK.get(model)
    if pricing is None:
        log.warning("agent_cost_unknown_model", model=model)
        # Treat as zero cost rather than guessing wrong; still log so we
        # notice the missing entry.
        input_rate = output_rate = 0.0
    else:
        input_rate, output_rate = pricing

    call_cost = (
        (input_tokens / 1_000_000) * input_rate
        + (output_tokens / 1_000_000) * output_rate
    )

    acc.input_tokens += input_tokens
    acc.output_tokens += output_tokens
    acc.cache_read_tokens += cache_read_tokens
    acc.cache_write_tokens += cache_write_tokens
    acc.usd_cost += call_cost

    bucket = acc.by_model.setdefault(
        model, {"input": 0, "output": 0, "usd": 0.0}
    )
    bucket["input"] = int(bucket["input"]) + input_tokens  # type: ignore[arg-type]
    bucket["output"] = int(bucket["output"]) + output_tokens  # type: ignore[arg-type]
    bucket["usd"] = float(bucket["usd"]) + call_cost  # type: ignore[arg-type]


def finalize_run_accumulator() -> dict | None:
    """Return the accumulated usage + cost dict, or None if no accumulator
    was started for this run (defensive)."""
    acc = _usage_var.get()
    if acc is None:
        return None
    return {
        "input_tokens": acc.input_tokens,
        "output_tokens": acc.output_tokens,
        "cache_read_tokens": acc.cache_read_tokens,
        "cache_write_tokens": acc.cache_write_tokens,
        "total_cost_usd": round(acc.usd_cost, 6),
        "sales_cost_usd": round(acc.usd_cost * SALES_COST_MULTIPLIER, 6),
        "by_model": acc.by_model,
    }


__all__ = [
    "start_run_accumulator",
    "add_usage",
    "finalize_run_accumulator",
    "SALES_COST_MULTIPLIER",
]
