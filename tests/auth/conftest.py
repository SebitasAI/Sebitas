"""Local fixtures for the auth integration tests.

The app uses a module-level async engine (`app.db.engine.engine`) whose
connection pool binds to whatever event loop first used it. pytest-asyncio's
default function-scoped loop means each test runs on a fresh loop, so pooled
connections from a prior test belong to a now-closed loop ("Future attached
to a different loop"). Disposing the pool at each test's teardown -- on the
same loop that created the connections -- closes them cleanly and leaves the
next test an empty pool to refill on its own loop.
"""

from __future__ import annotations

import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_pool_after_test():
    yield
    from app.db.engine import engine

    await engine.dispose()
