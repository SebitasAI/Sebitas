"""Unit tests for the /api/billing endpoints. The DB + Clerk + Stripe
SDK layers are mocked; we pin the request validation + response
shaping + the 'Stripe not configured' downgrade paths."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_clerk_override(monkeypatch):
    """Build a FastAPI app with billing router + Clerk auth overridden
    to return a fixed workspace/user. Avoids real JWT verification.

    The default override grants admin role so the existing checkout /
    portal tests don't run into the RBAC gate. Tests that need to
    exercise the non-admin path explicitly re-override
    `require_workspace_admin` to raise 403."""
    from app.api.billing import router
    from app.auth.clerk import ClerkClaims, ResolvedAppUser, require_app_user
    from app.auth.rbac import require_workspace_admin

    app = FastAPI()
    app.include_router(router)

    workspace_id = uuid.uuid4()
    app_user_id = uuid.uuid4()
    fake = ResolvedAppUser(
        workspace_id=workspace_id,
        app_user_id=app_user_id,
        clerk_user_id="user_test",
        email="sam@misterr.app",
    )
    admin_claims = ClerkClaims(
        sub="user_test",
        email="sam@misterr.app",
        org_id="org_test",
        org_role="org:admin",
        org_slug="test",
        raw={},
    )

    async def _fake_require():
        return fake

    async def _fake_admin():
        return admin_claims

    app.dependency_overrides[require_app_user] = _fake_require
    app.dependency_overrides[require_workspace_admin] = _fake_admin
    return app, fake


class TestCheckoutValidation:
    def test_checkout_rejects_free_plan(self, app_with_clerk_override, monkeypatch):
        from app.billing import stripe_client

        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(stripe_client, "get_price_ids", lambda: {})

        app, _ = app_with_clerk_override
        client = TestClient(app)
        r = client.post("/api/billing/checkout", json={"plan": "free", "cycle": "monthly"})
        assert r.status_code == 400
        assert "purchasable" in r.json()["detail"]

    def test_checkout_rejects_unlimited_plan(self, app_with_clerk_override, monkeypatch):
        from app.billing import stripe_client

        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(stripe_client, "get_price_ids", lambda: {})

        app, _ = app_with_clerk_override
        client = TestClient(app)
        r = client.post(
            "/api/billing/checkout", json={"plan": "unlimited", "cycle": "monthly"}
        )
        assert r.status_code == 400

    def test_checkout_rejects_unknown_plan(self, app_with_clerk_override, monkeypatch):
        from app.billing import stripe_client

        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(stripe_client, "get_price_ids", lambda: {})

        app, _ = app_with_clerk_override
        client = TestClient(app)
        r = client.post(
            "/api/billing/checkout", json={"plan": "premium_ultra", "cycle": "monthly"}
        )
        assert r.status_code == 400

    def test_checkout_rejects_unknown_cycle(self, app_with_clerk_override, monkeypatch):
        # FastAPI's pydantic Literal validation handles this -> 422
        from app.billing import stripe_client

        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(stripe_client, "get_price_ids", lambda: {})

        app, _ = app_with_clerk_override
        client = TestClient(app)
        r = client.post(
            "/api/billing/checkout", json={"plan": "starter", "cycle": "weekly"}
        )
        assert r.status_code == 422

    def test_checkout_503_when_stripe_unconfigured(
        self, app_with_clerk_override, monkeypatch
    ):
        from app.billing import stripe_client

        monkeypatch.setattr(stripe_client, "is_configured", lambda: False)

        app, _ = app_with_clerk_override
        client = TestClient(app)
        r = client.post(
            "/api/billing/checkout", json={"plan": "starter", "cycle": "monthly"}
        )
        assert r.status_code == 503

    def test_checkout_503_when_price_missing(self, app_with_clerk_override, monkeypatch):
        from app.billing import stripe_client

        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(stripe_client, "get_price_ids", lambda: {})  # no IDs loaded

        app, _ = app_with_clerk_override
        client = TestClient(app)
        r = client.post(
            "/api/billing/checkout", json={"plan": "starter", "cycle": "monthly"}
        )
        assert r.status_code == 503
        assert "setup_stripe_catalog" in r.json()["detail"]


class TestPortalValidation:
    def test_portal_503_when_stripe_unconfigured(
        self, app_with_clerk_override, monkeypatch
    ):
        from app.billing import stripe_client

        monkeypatch.setattr(stripe_client, "is_configured", lambda: False)

        app, _ = app_with_clerk_override
        client = TestClient(app)
        r = client.post("/api/billing/portal")
        assert r.status_code == 503

    def test_portal_400_when_no_customer(self, app_with_clerk_override, monkeypatch):
        from app.api import billing as api_billing
        from app.billing import stripe_client

        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(
            api_billing, "_resolve_stripe_customer", AsyncMock(return_value=None)
        )

        app, _ = app_with_clerk_override
        client = TestClient(app)
        r = client.post("/api/billing/portal")
        assert r.status_code == 400
        assert "No active Stripe Customer" in r.json()["detail"]


class TestPlanOptionsBuilder:
    def test_builds_four_paid_plans(self, monkeypatch):
        from app.api.billing import _build_plan_options
        from app.billing import stripe_client

        # Pretend all 8 prices are configured.
        monkeypatch.setattr(
            stripe_client,
            "get_price_ids",
            lambda: {
                f"{p}_{c}": f"price_{p}_{c}"
                for p in ("starter", "pro", "scale", "business")
                for c in ("monthly", "annual")
            },
        )

        opts = _build_plan_options()
        names = [o.name for o in opts]
        assert names == ["starter", "pro", "scale", "business"]
        for o in opts:
            assert o.has_monthly_checkout is True
            assert o.has_annual_checkout is True
            assert o.annual_price_floor == pytest.approx(
                o.monthly_price_floor * 12 * 0.8
            )

    def test_marks_missing_checkout_when_price_unset(self, monkeypatch):
        from app.api.billing import _build_plan_options
        from app.billing import stripe_client

        monkeypatch.setattr(stripe_client, "get_price_ids", lambda: {})
        opts = _build_plan_options()
        for o in opts:
            assert o.has_monthly_checkout is False
            assert o.has_annual_checkout is False


class TestAdminGate:
    """Both /checkout and /portal must 403 when the caller isn't org:admin.
    Other behaviour (validation, Stripe not configured) is covered above
    under the default 'admin' fixture."""

    def _app_with_member(self):
        from app.api.billing import router
        from app.auth.clerk import ClerkClaims, ResolvedAppUser, require_app_user
        from app.auth.rbac import require_workspace_admin
        from fastapi import HTTPException

        app = FastAPI()
        app.include_router(router)
        fake = ResolvedAppUser(
            workspace_id=uuid.uuid4(),
            app_user_id=uuid.uuid4(),
            clerk_user_id="user_member",
            email="member@misterr.app",
        )
        member_claims = ClerkClaims(
            sub="user_member",
            email="member@misterr.app",
            org_id="org_test",
            org_role="org:member",
            org_slug="test",
            raw={},
        )

        async def _fake_require():
            return fake

        async def _fake_admin():
            # Mirrors the real require_workspace_admin: 403 for non-admins.
            if member_claims.org_role != "org:admin":
                raise HTTPException(status_code=403, detail="Only workspace admins can perform this action.")
            return member_claims

        app.dependency_overrides[require_app_user] = _fake_require
        app.dependency_overrides[require_workspace_admin] = _fake_admin
        return app

    def test_checkout_forbidden_for_member(self, monkeypatch):
        from app.billing import stripe_client

        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(stripe_client, "get_price_ids", lambda: {})
        client = TestClient(self._app_with_member())
        r = client.post(
            "/api/billing/checkout", json={"plan": "starter", "cycle": "monthly"}
        )
        assert r.status_code == 403

    def test_portal_forbidden_for_member(self, monkeypatch):
        from app.billing import stripe_client

        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        client = TestClient(self._app_with_member())
        r = client.post("/api/billing/portal")
        assert r.status_code == 403
