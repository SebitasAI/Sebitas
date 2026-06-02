"""Unit tests for pipedream.resolve_app_slug.

The HTTP layer is stubbed via monkeypatch so the tests never hit
api.pipedream.com. What matters is the resolution priority chain:

  1. Exact name_slug match
  2. Prefix match (`<slug>_*`)
  3. Token match (slug appears as a `_`-separated token)
  4. Top-1 by relevance
  5. Last resort: original slug returned, no crash
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations import pipedream as pd


def _fake_apps_response(items: list[dict]):
    """Build a context manager that mimics aiohttp's response shape so
    `async with session.get(...) as resp: data = await resp.json()` works."""
    resp = MagicMock()
    resp.status = 200
    resp.json = AsyncMock(return_value={"data": items})
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.fixture(autouse=True)
def _reset_cache():
    pd._slug_cache.clear()
    yield
    pd._slug_cache.clear()


@pytest.mark.asyncio
async def test_exact_match_wins():
    items = [
        {"name_slug": "gong", "name": "Gong"},
        {"name_slug": "gong_engage", "name": "Gong Engage"},
    ]
    with patch("app.integrations.pipedream._headers", new=AsyncMock(return_value={})), \
         patch("app.integrations.pipedream.aiohttp.ClientSession") as session_cls:
        session = session_cls.return_value
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=_fake_apps_response(items))

        result = await pd.resolve_app_slug("gong")
    assert result == "gong"


@pytest.mark.asyncio
async def test_prefix_match_for_compound_slug():
    """The Salesforce case: input `salesforce`, Pipedream's slug is
    `salesforce_rest_api`."""
    items = [
        {"name_slug": "salesforce_rest_api", "name": "Salesforce"},
        {"name_slug": "salesforce_pardot", "name": "Salesforce Pardot"},
    ]
    with patch("app.integrations.pipedream._headers", new=AsyncMock(return_value={})), \
         patch("app.integrations.pipedream.aiohttp.ClientSession") as session_cls:
        session = session_cls.return_value
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=_fake_apps_response(items))

        result = await pd.resolve_app_slug("salesforce")
    assert result == "salesforce_rest_api"


@pytest.mark.asyncio
async def test_token_match_when_not_prefix():
    """User slug is a non-leading token in the Pipedream slug."""
    items = [
        # Synthetic: input 'crm', actual slug 'sugar_crm'.
        {"name_slug": "sugar_crm", "name": "Sugar CRM"},
    ]
    with patch("app.integrations.pipedream._headers", new=AsyncMock(return_value={})), \
         patch("app.integrations.pipedream.aiohttp.ClientSession") as session_cls:
        session = session_cls.return_value
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=_fake_apps_response(items))

        result = await pd.resolve_app_slug("crm")
    assert result == "sugar_crm"


@pytest.mark.asyncio
async def test_top1_fallback_when_no_structural_match():
    """No exact / prefix / token match: trust Pipedream's q-rank top-1."""
    items = [
        {"name_slug": "completely_different_slug", "name": "Best Match"},
        {"name_slug": "another", "name": "Other"},
    ]
    with patch("app.integrations.pipedream._headers", new=AsyncMock(return_value={})), \
         patch("app.integrations.pipedream.aiohttp.ClientSession") as session_cls:
        session = session_cls.return_value
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=_fake_apps_response(items))

        result = await pd.resolve_app_slug("xyz")
    assert result == "completely_different_slug"


@pytest.mark.asyncio
async def test_returns_original_when_no_results():
    """No matches at all -> pass through the original slug so the
    downstream connect URL fails with a clear error rather than
    silently routing to a wrong app."""
    with patch("app.integrations.pipedream._headers", new=AsyncMock(return_value={})), \
         patch("app.integrations.pipedream.aiohttp.ClientSession") as session_cls:
        session = session_cls.return_value
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=_fake_apps_response([]))

        result = await pd.resolve_app_slug("zzzz-fake")
    assert result == "zzzz-fake"


@pytest.mark.asyncio
async def test_returns_original_on_http_error():
    """Non-200 from the catalog endpoint -> pass through, don't crash."""
    resp = MagicMock()
    resp.status = 500
    resp.json = AsyncMock(return_value={})
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch("app.integrations.pipedream._headers", new=AsyncMock(return_value={})), \
         patch("app.integrations.pipedream.aiohttp.ClientSession") as session_cls:
        session = session_cls.return_value
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=cm)

        result = await pd.resolve_app_slug("salesforce")
    assert result == "salesforce"


@pytest.mark.asyncio
async def test_returns_original_on_exception():
    """Network error or anything else -> graceful pass-through."""
    with patch("app.integrations.pipedream._headers", new=AsyncMock(return_value={})), \
         patch("app.integrations.pipedream.aiohttp.ClientSession", side_effect=RuntimeError("boom")):
        result = await pd.resolve_app_slug("salesforce")
    assert result == "salesforce"


@pytest.mark.asyncio
async def test_caches_resolved_slug():
    """Two consecutive calls hit the network once."""
    items = [{"name_slug": "salesforce_rest_api", "name": "Salesforce"}]
    with patch("app.integrations.pipedream._headers", new=AsyncMock(return_value={})), \
         patch("app.integrations.pipedream.aiohttp.ClientSession") as session_cls:
        session = session_cls.return_value
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=_fake_apps_response(items))

        r1 = await pd.resolve_app_slug("salesforce")
        r2 = await pd.resolve_app_slug("salesforce")
    assert r1 == r2 == "salesforce_rest_api"
    # ClientSession should have been invoked only once.
    assert session_cls.call_count == 1


@pytest.mark.asyncio
async def test_empty_slug_passes_through():
    result = await pd.resolve_app_slug("")
    assert result == ""
