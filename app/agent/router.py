"""Model routing via LiteLLM.

Claude (Opus, primary) is called through the Anthropic SDK directly (see
agent/claude.py) to preserve prompt caching + adaptive thinking. LiteLLM is used
here only to route delegated, simple sub-tasks to a cheaper/faster model (Haiku
by default) where thinking/caching are not needed. This is also the seam for
adding non-Anthropic providers later without touching the agent loop.
"""

from __future__ import annotations

import litellm
import structlog
from langfuse import get_client

from app.config import get_settings

log = structlog.get_logger(__name__)
_langfuse = get_client()

# Let LiteLLM read provider keys (ANTHROPIC_API_KEY, etc.) from the environment.
litellm.drop_params = True  # silently drop params a given model does not support


async def run_cheap(task: str) -> str:
    """Run a self-contained simple task on the cheap model via LiteLLM."""
    settings = get_settings()
    model = settings.cheap_model
    with _langfuse.start_as_current_observation(
        as_type="generation",
        name="route:cheap-model",
        model=model,
        input=task,
    ) as gen:
        response = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": task}],
            max_tokens=1024,
        )
        text = (response.choices[0].message.content or "").strip()
        usage = getattr(response, "usage", None)
        if usage is not None:
            input_t = getattr(usage, "prompt_tokens", 0) or 0
            output_t = getattr(usage, "completion_tokens", 0) or 0
            gen.update(
                output=text,
                usage_details={"input": input_t, "output": output_t},
            )
            # Feed the per-run cost accumulator so cheap-model delegations
            # show up in the final sales_cost score alongside Opus turns.
            from app.agent import cost as _cost

            _cost.add_usage(
                model=model, input_tokens=input_t, output_tokens=output_t
            )
        else:
            gen.update(output=text)
    log.info("route_cheap_done", model=model)
    return text or "(sin respuesta del modelo barato)"
