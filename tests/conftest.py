"""Shared fixtures for the Skills tests.

Two scopes:

- **Unit fixtures** (always loaded): a fake R2 module replacement so storage
  tests + registry tests don't touch real R2; a LiteLLM patcher so the
  frontmatter generator can be coerced into any response shape.
- **Integration fixtures** (only when TEST_DATABASE_URL is set): real
  Postgres connection + a fresh `workspace` + `app_user` rows per test. The
  fixture uses the same migrations that run in prod so the schema matches.

Tests that need a DB use the `db_session` fixture or one of the helper
fixtures (`workspace`, `user_a`, `user_b`). Without TEST_DATABASE_URL set,
those tests are skipped with a clear marker.
"""

from __future__ import annotations

import os

# Dummy env vars so `app.config.Settings()` validates at import time without
# the host environment having real production secrets. Tests that need real
# values (DB, R2, Anthropic) override these via fixtures or environment.
# Must be set BEFORE any `app.*` import below so module-level config loads
# don't fail collection.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic")
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("TEST_DATABASE_URL") or "postgresql+asyncpg://localhost/none",
)
os.environ.setdefault("SLACK_APP_TOKEN", "xapp-test")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("R2_ACCOUNT_ID", "test")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("R2_BUCKET", "test-bucket")

# Hard-isolate Langfuse from the test process. Tests deliberately raise
# inside patched paths (`RuntimeError("boom")` etc.) to exercise error
# handling. Without this block, running `doppler run -- pytest` (with
# prod LANGFUSE_* env vars in scope) sends those fixture exceptions to
# the prod Langfuse project, polluting telemetry and confusing the
# dashboards. We unconditionally OVERRIDE (not setdefault) the relevant
# keys so the SDK initializes as a no-op regardless of host env. Devs
# wanting to test against a real Langfuse project can opt in by setting
# `LANGFUSE_TEST_ENABLED=true` before `pytest`.
if os.environ.get("LANGFUSE_TEST_ENABLED", "").lower() not in ("1", "true", "yes"):
    # `LANGFUSE_ENABLED=false` disables the SDK entirely (no OTel
    # exporter init, no background flush). Setting it BEFORE
    # `langfuse.get_client()` is imported anywhere is mandatory.
    os.environ["LANGFUSE_ENABLED"] = "false"
    # Belt-and-braces: also clear the credentials so any code path
    # that bypasses the enabled-flag check still has no way to
    # authenticate to prod.
    for _k in (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_HOST",
        "LANGFUSE_BASE_URL",
    ):
        os.environ[_k] = ""

import uuid  # noqa: E402
from typing import Any  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402


# --------------------------------------------------------------------------- #
# Fake R2 (always active)
# --------------------------------------------------------------------------- #

class _FakeR2:
    """In-memory replacement for the boto3-backed R2 module. Tests inject it
    by monkey-patching `app.artifacts.r2.put_bytes` etc.; we keep the surface
    minimal (put_bytes / get_bytes / get_text + delete_object via _client)
    matching what `app.skills.storage` uses."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put_bytes(self, key: str, data: bytes, content_type: str = "") -> None:
        self.objects[key] = data

    async def get_bytes(self, key: str) -> bytes:
        if key not in self.objects:
            raise KeyError(key)
        return self.objects[key]

    async def get_text(self, key: str) -> str:
        return (await self.get_bytes(key)).decode("utf-8")

    def client_factory(self):
        outer = self

        class _Client:
            def delete_object(self, *, Bucket: str, Key: str) -> None:  # noqa: N803
                outer.objects.pop(Key, None)

        return _Client()


@pytest.fixture
def fake_r2(monkeypatch):
    """Patch app.artifacts.r2 + clear app.skills.storage's LRU."""
    from app.artifacts import r2 as r2_module
    from app.skills import storage as storage_module

    fake = _FakeR2()
    monkeypatch.setattr(r2_module, "put_bytes", fake.put_bytes)
    monkeypatch.setattr(r2_module, "get_bytes", fake.get_bytes)
    monkeypatch.setattr(r2_module, "get_text", fake.get_text)
    monkeypatch.setattr(r2_module, "_client", lambda: fake.client_factory())
    # Reset module-level LRU cache so test order can't leak entries.
    storage_module._body_cache.clear()
    return fake


@pytest.fixture
def patch_litellm(monkeypatch):
    """Return a helper that swaps `litellm.acompletion` in the frontmatter
    module with an AsyncMock returning the given text. Tests pass a closure
    or string; the mock records calls so assertions can check the prompt."""
    from app.skills import frontmatter as fm_module

    def _patch(reply_text: str | None = None, side_effect: Any = None) -> AsyncMock:
        mock = AsyncMock()
        if side_effect is not None:
            mock.side_effect = side_effect
        else:
            # Shape that mirrors litellm's actual response object.
            class _Msg:
                def __init__(self, c: str) -> None:
                    self.content = c

            class _Choice:
                def __init__(self, c: str) -> None:
                    self.message = _Msg(c)

            class _Usage:
                prompt_tokens = 100
                completion_tokens = 50

            class _Resp:
                def __init__(self, c: str) -> None:
                    self.choices = [_Choice(c)]
                    self.usage = _Usage()

            mock.return_value = _Resp(reply_text or "{}")
        monkeypatch.setattr(fm_module.litellm, "acompletion", mock)
        return mock

    return _patch


# --------------------------------------------------------------------------- #
# DB integration fixtures
# --------------------------------------------------------------------------- #

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

_db_skip_reason = (
    "TEST_DATABASE_URL not set; set it to a Postgres URL "
    "(e.g. postgresql+asyncpg://localhost/misterr_test) to run integration tests"
)


def _maybe_skip_db():
    if not TEST_DATABASE_URL:
        pytest.skip(_db_skip_reason)


@pytest_asyncio.fixture
async def db_session():
    """Yields a SQLAlchemy AsyncSession bound to TEST_DATABASE_URL with all
    operations rolled back at the end. Schema must already exist (run
    alembic upgrade head against the test DB once before running these)."""
    _maybe_skip_db()
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine(TEST_DATABASE_URL, future=True)
    async with engine.connect() as conn:
        trans = await conn.begin()
        async with AsyncSession(bind=conn, expire_on_commit=False) as session:
            try:
                yield session
            finally:
                await trans.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def workspace(db_session):
    """Create a workspace with a unique slack_team_id per test."""
    from app.db.models import Workspace

    team_id = f"T{uuid.uuid4().hex[:10].upper()}"
    ws = Workspace(slack_team_id=team_id, name="test-workspace")
    db_session.add(ws)
    await db_session.flush()
    return ws


@pytest_asyncio.fixture
async def user_a(db_session, workspace):
    from app.db.models import AppUser
    u = AppUser(workspace_id=workspace.id, slack_user_id=f"U{uuid.uuid4().hex[:10].upper()}")
    db_session.add(u)
    await db_session.flush()
    return u


@pytest_asyncio.fixture
async def user_b(db_session, workspace):
    from app.db.models import AppUser
    u = AppUser(workspace_id=workspace.id, slack_user_id=f"U{uuid.uuid4().hex[:10].upper()}")
    db_session.add(u)
    await db_session.flush()
    return u
