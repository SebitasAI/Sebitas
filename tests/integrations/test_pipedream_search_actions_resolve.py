"""Pin the contract: `pipedream.search_actions` resolves the
user-friendly slug to the provider's `name_slug` before hitting the
catalog endpoint.

This is the same divergence-bug class as the connect-link issue fixed
in PR #122 (`salesforce -> salesforce_rest_api`, `slack -> slack_bot`,
etc.). Without resolution, searches for divergent slugs return 0
actions and downstream code (catalog_skills generator,
gateway.find_actions, daily catalog sweeper) silently produces empty
catalogs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations import pipedream as pd


def _fake_json_response(payload):
    resp = MagicMock()
    resp.status = 200
    resp.json = AsyncMock(return_value=payload)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.fixture(autouse=True)
def _reset_slug_cache():
    pd._slug_cache.clear()
    yield
    pd._slug_cache.clear()


@pytest.mark.asyncio
async def test_search_actions_resolves_divergent_slug():
    """When our slug is `salesforce` but Pipedream's is
    `salesforce_rest_api`, the GET to /actions must use the resolved
    slug, not the input."""
    captured: list[dict] = []

    async def fake_resolve(s):
        if s == "salesforce":
            return "salesforce_rest_api"
        return s

    def fake_get(url, params=None, headers=None):
        captured.append({"url": url, "params": params})
        return _fake_json_response({"data": [
            {"key": "salesforce_rest_api-get-current-user"},
            {"key": "salesforce_rest_api-list-records"},
        ]})

    with patch("app.integrations.pipedream.resolve_app_slug", new=fake_resolve), \
         patch("app.integrations.pipedream._headers", new=AsyncMock(return_value={})), \
         patch("app.integrations.pipedream.aiohttp.ClientSession") as session_cls:
        session = session_cls.return_value
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.get = fake_get

        actions = await pd.search_actions("salesforce")

    assert len(actions) == 2
    # The HTTP call must have used the RESOLVED slug.
    assert captured[0]["params"]["app"] == "salesforce_rest_api"
    assert captured[0]["params"]["app"] != "salesforce"


@pytest.mark.asyncio
async def test_search_actions_passes_through_when_slug_matches():
    """When our slug already matches Pipedream's, nothing changes."""
    captured: list[dict] = []

    async def fake_resolve(s):
        return s  # No divergence for gong.

    def fake_get(url, params=None, headers=None):
        captured.append({"url": url, "params": params})
        return _fake_json_response({"data": [{"key": "gong-list-calls"}]})

    with patch("app.integrations.pipedream.resolve_app_slug", new=fake_resolve), \
         patch("app.integrations.pipedream._headers", new=AsyncMock(return_value={})), \
         patch("app.integrations.pipedream.aiohttp.ClientSession") as session_cls:
        session = session_cls.return_value
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.get = fake_get

        actions = await pd.search_actions("gong")

    assert len(actions) == 1
    assert captured[0]["params"]["app"] == "gong"


@pytest.mark.asyncio
async def test_search_actions_forwards_query_param():
    """The optional `q` filter must reach the API alongside the
    resolved slug."""
    captured: list[dict] = []

    async def fake_resolve(s):
        return "slack_bot" if s == "slack" else s

    def fake_get(url, params=None, headers=None):
        captured.append({"url": url, "params": params})
        return _fake_json_response({"data": []})

    with patch("app.integrations.pipedream.resolve_app_slug", new=fake_resolve), \
         patch("app.integrations.pipedream._headers", new=AsyncMock(return_value={})), \
         patch("app.integrations.pipedream.aiohttp.ClientSession") as session_cls:
        session = session_cls.return_value
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.get = fake_get

        await pd.search_actions("slack", "send message")

    assert captured[0]["params"]["app"] == "slack_bot"
    assert captured[0]["params"]["q"] == "send message"


@pytest.mark.asyncio
async def test_search_actions_when_resolve_fails_uses_original():
    """If the slug resolver errors and returns the original slug, the
    catalog query still runs (likely returning 0). We don't crash."""
    async def fake_resolve(s):
        return s  # Simulates resolve_app_slug fallback path.

    def fake_get(url, params=None, headers=None):
        return _fake_json_response({"data": []})

    with patch("app.integrations.pipedream.resolve_app_slug", new=fake_resolve), \
         patch("app.integrations.pipedream._headers", new=AsyncMock(return_value={})), \
         patch("app.integrations.pipedream.aiohttp.ClientSession") as session_cls:
        session = session_cls.return_value
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.get = fake_get

        actions = await pd.search_actions("some-unknown")
    assert actions == []
