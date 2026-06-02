"""Unit tests for `gateway.find_in_action`.

The provider is stubbed (no Pipedream / Composio round-trips). What
the test pins down:

  - sweeps windows backwards from `end_iso` (or now)
  - merges `base_params` with the date window per iteration
  - strips date params from `base_params` so the caller can't double-set them
  - returns the first window with matches; emits a hit footer naming
    the iteration + window
  - exhausts at `max_iterations` and returns a not-found diagnostic
  - validates filter_substring + date param names
  - clamps max_iterations at the hard ceiling
"""

from __future__ import annotations

import pytest

from app.integrations import gateway as gw


@pytest.fixture(autouse=True)
def _stub_run_action(monkeypatch):
    """Replace `gateway.run_action` with a recorder + scriptable
    response so the test never makes real network calls."""
    calls: list[dict] = []
    # By default, no matches. Override `scripted` per-test.
    state = {"scripted": ["[filter] needle='x': 0 of 5 items matched\n\n[]"] * 100}

    async def _fake(app, action_id, params=None, *, filter_substring=None):
        calls.append({
            "app": app,
            "action_id": action_id,
            "params": dict(params or {}),
            "filter_substring": filter_substring,
        })
        if not state["scripted"]:
            return "[filter] needle='x': 0 of 0 items matched\n\n[]"
        return state["scripted"].pop(0)

    monkeypatch.setattr(gw, "run_action", _fake)
    yield calls, state


@pytest.mark.asyncio
async def test_hits_first_window_when_match_present(_stub_run_action):
    calls, state = _stub_run_action
    state["scripted"] = [
        "[filter] needle='meli': 1 of 5 items matched\n\n[{'id': 1, 'name': 'Mercadolibre Q4'}]"
    ]
    out = await gw.find_in_action(
        "gong", "gong-get-extensive-data",
        filter_substring="meli",
        base_params={"includeParties": True},
        window_days=2, max_iterations=5,
        end_iso="2026-06-02T23:59:59Z",
    )
    assert "[find_in_action] hit on iteration 1/5" in out
    assert "1 matched / 5 scanned" in out
    assert "Mercadolibre Q4" in out
    assert len(calls) == 1
    # Date window was passed in.
    assert "fromDateTime" in calls[0]["params"]
    assert "toDateTime" in calls[0]["params"]
    assert calls[0]["params"]["toDateTime"] == "2026-06-02T23:59:59Z"
    # base_params were merged.
    assert calls[0]["params"]["includeParties"] is True
    assert calls[0]["filter_substring"] == "meli"


@pytest.mark.asyncio
async def test_walks_until_hit(_stub_run_action):
    calls, state = _stub_run_action
    state["scripted"] = [
        "[filter] needle='meli': 0 of 5 items matched\n\n[]",
        "[filter] needle='meli': 0 of 5 items matched\n\n[]",
        "[filter] needle='meli': 2 of 8 items matched\n\n[{'id': 9}]",
    ]
    out = await gw.find_in_action(
        "gong", "gong-get-extensive-data",
        filter_substring="meli",
        window_days=1, max_iterations=10,
        end_iso="2026-06-02T23:59:59Z",
    )
    assert "[find_in_action] hit on iteration 3/10" in out
    assert len(calls) == 3
    # Windows must walk backwards: each fromDateTime should be earlier
    # than the previous one.
    froms = [c["params"]["fromDateTime"] for c in calls]
    assert froms == sorted(froms, reverse=True)


@pytest.mark.asyncio
async def test_exhausts_and_reports_not_found(_stub_run_action):
    calls, state = _stub_run_action
    state["scripted"] = [
        "[filter] needle='nope': 0 of 5 items matched\n\n[]",
    ] * 5
    out = await gw.find_in_action(
        "gong", "gong-get-extensive-data",
        filter_substring="nope",
        max_iterations=5,
        end_iso="2026-06-02T23:59:59Z",
    )
    assert "[find_in_action] no match" in out
    assert "5 window" in out
    assert len(calls) == 5


@pytest.mark.asyncio
async def test_strips_date_params_from_base_params(_stub_run_action):
    calls, state = _stub_run_action
    state["scripted"] = [
        "[filter] needle='x': 1 of 1 items matched\n\n[{'id': 1}]",
    ]
    await gw.find_in_action(
        "gong", "gong-get-extensive-data",
        filter_substring="x",
        base_params={
            "fromDateTime": "2020-01-01T00:00:00Z",  # should be overridden
            "toDateTime": "2020-01-02T00:00:00Z",
            "context": "Extended",
        },
        end_iso="2026-06-02T23:59:59Z",
    )
    # The agent's bogus 2020 date must not survive.
    assert calls[0]["params"]["fromDateTime"].startswith("2026-")
    assert calls[0]["params"]["toDateTime"].startswith("2026-")
    # But other base_params survive.
    assert calls[0]["params"]["context"] == "Extended"


@pytest.mark.asyncio
async def test_custom_date_param_names(_stub_run_action):
    calls, state = _stub_run_action
    state["scripted"] = [
        "[filter] needle='x': 1 of 1 items matched\n\n[{'id': 1}]",
    ]
    await gw.find_in_action(
        "hubspot", "hubspot-list-tickets",
        filter_substring="x",
        date_from_param="createdAfter",
        date_to_param="createdBefore",
        end_iso="2026-06-02T23:59:59Z",
    )
    assert "createdAfter" in calls[0]["params"]
    assert "createdBefore" in calls[0]["params"]
    # Default Gong-style names must NOT leak.
    assert "fromDateTime" not in calls[0]["params"]
    assert "toDateTime" not in calls[0]["params"]


@pytest.mark.asyncio
async def test_validation_empty_filter():
    out = await gw.find_in_action(
        "gong", "gong-x", filter_substring="",
    )
    assert "Error" in out
    assert "filter_substring" in out


@pytest.mark.asyncio
async def test_validation_blank_date_param():
    out = await gw.find_in_action(
        "gong", "gong-x", filter_substring="meli",
        date_from_param="", date_to_param="toDateTime",
    )
    assert "Error" in out
    assert "date_from_param" in out or "date_to_param" in out


@pytest.mark.asyncio
async def test_validation_bad_end_iso():
    out = await gw.find_in_action(
        "gong", "gong-x", filter_substring="meli",
        end_iso="not-a-date",
    )
    assert "Error" in out
    assert "end_iso" in out


@pytest.mark.asyncio
async def test_caps_max_iterations(_stub_run_action):
    calls, state = _stub_run_action
    state["scripted"] = [
        "[filter] needle='x': 0 of 1 items matched\n\n[]"
    ] * 200
    await gw.find_in_action(
        "gong", "gong-x", filter_substring="x",
        max_iterations=9999,  # ridiculous, should be clamped
        end_iso="2026-06-02T23:59:59Z",
    )
    # Hard cap is `_FIND_MAX_ITERATIONS`.
    assert len(calls) == gw._FIND_MAX_ITERATIONS
