"""Datadog HTTP-direct integration.

Covers endpoints that the Pipedream connector doesn't expose (notably the
metrics query endpoint `/api/v1/query` used by every Datadog dashboard widget).
With this tool the agent can answer most metric / APM questions the user
throws at it (counts, sums, top-N, error rates, etc.) by composing a Datadog
query string.

Auth uses the per-deployment Datadog API key + Application key from Doppler.
We do NOT support per-workspace Datadog credentials in this slice -- a single
shared Datadog org is the trade-off for getting this working today. If you
need multi-tenant Datadog later, plug it in via the IntegrationProvider
abstraction (same shape as `pipedream_provider.py`).
"""

from __future__ import annotations

from typing import Any

import aiohttp
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)


class DatadogError(Exception):
    """Raised on any non-2xx from Datadog or missing config. The caller (the
    agent tool) maps to a user-facing message."""


def _base_url() -> str:
    return f"https://api.{get_settings().dd_site}"


def _headers() -> dict[str, str]:
    s = get_settings()
    if not s.dd_api_key or not s.dd_app_key:
        raise DatadogError(
            "Datadog not configured (missing DD_API_KEY or DD_APP_KEY in Doppler)."
        )
    return {
        "DD-API-KEY": s.dd_api_key,
        "DD-APPLICATION-KEY": s.dd_app_key,
        "Content-Type": "application/json",
    }


async def query_metrics(
    query: str, from_seconds_ago: int = 3600, to_seconds_ago: int = 0
) -> dict[str, Any]:
    """Hit `/api/v1/query` and return the raw result dict.

    `query`: a Datadog query string. Examples:
      - `sum:users.unique{*}` -- total unique users.
      - `avg:trace.web.request.duration{*} by {service}` -- avg latency per service.
      - `top(sum:trace.web.request.errors{*} by {resource_name}, 10, 'sum', 'desc')`
         -- top 10 resources by total errors.
    `from_seconds_ago` / `to_seconds_ago`: time window relative to now.

    Time window must satisfy `from > to` (Datadog: from is older, to is newer).
    Default = last hour.
    """
    import time

    if not query or not query.strip():
        raise DatadogError("query is required")
    now = int(time.time())
    from_ts = now - max(from_seconds_ago, 1)
    to_ts = now - max(to_seconds_ago, 0)
    url = f"{_base_url()}/api/v1/query"
    params = {"query": query, "from": str(from_ts), "to": str(to_ts)}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params=params, headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                data = await resp.json()
                if resp.status >= 400:
                    raise DatadogError(f"Datadog {resp.status}: {str(data)[:300]}")
    except aiohttp.ClientError as exc:
        raise DatadogError(f"network error reaching Datadog: {exc}") from exc

    if isinstance(data, dict) and data.get("status") == "error":
        raise DatadogError(f"Datadog returned error: {data.get('error', '')[:300]}")
    return data


def format_query_result(result: dict[str, Any], query: str) -> str:
    """Turn the raw `/api/v1/query` response into a Slack-readable text. Picks
    the most informative summary per series (last value + total). Multi-series
    results (group-by, top-N) get one bullet per scope."""
    series = result.get("series") or []
    if not series:
        return f"Query `{query}` corrió OK pero no devolvió series (sin datos en la ventana)."

    lines: list[str] = [f"Query: `{query}` — {len(series)} serie(s):"]
    for s in series[:15]:
        scope = s.get("scope") or s.get("expression") or s.get("metric") or "?"
        points = s.get("pointlist") or []
        # Filter out nulls (Datadog returns [ts, null] for missing points).
        values = [p[1] for p in points if isinstance(p, list) and len(p) == 2 and p[1] is not None]
        if not values:
            lines.append(f"• `{scope}`: sin puntos válidos")
            continue
        last = values[-1]
        total = sum(values)
        avg = total / len(values)
        # Round nicely for ints vs floats.
        def fmt(v: float) -> str:
            return f"{v:.2f}" if abs(v - round(v)) > 1e-6 else str(int(v))
        lines.append(
            f"• `{scope}`: último={fmt(last)}, suma={fmt(total)}, "
            f"avg={fmt(avg)} ({len(values)} puntos)"
        )
    if len(series) > 15:
        lines.append(f"... y {len(series) - 15} series más (truncadas)")
    return "\n".join(lines)
