"""Integration tests for the billing repository. Need TEST_DATABASE_URL
to be set + migrations applied. Skipped otherwise (same pattern as
tests/automations/test_automations_integration.py).

Covers:
- `preflight_check`: unlimited / enterprise bypass; zero balance blocks;
  positive balance allows; missing plan row warns + allows.
- `debit_for_agent_run`: subtracts credits, appends ledger, idempotent
  on agent_run_id, skipped for unlimited.
- `credit_monthly_reset`: wipe-and-grant, ledger entry, last_reset_at."""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; integration tests skipped",
)


@pytest_asyncio.fixture
async def free_plan(db_session, workspace):
    """Workspace with a 'free' plan + a small balance. Default fixture
    for tests that want a metered customer."""
    from app.db.models import CreditBalance, WorkspaceBillingPlan

    plan = WorkspaceBillingPlan(
        workspace_id=workspace.id,
        plan="free",
        credits_per_month=50_000,
        price_usd_monthly=0,
    )
    balance = CreditBalance(workspace_id=workspace.id, balance_credits=10_000)
    db_session.add_all([plan, balance])
    await db_session.flush()
    return plan


@pytest_asyncio.fixture
async def unlimited_plan(db_session, workspace):
    """Workspace marked as 'unlimited' (pre-billing tenant like Simetrik)."""
    from app.db.models import CreditBalance, WorkspaceBillingPlan

    plan = WorkspaceBillingPlan(
        workspace_id=workspace.id,
        plan="unlimited",
        credits_per_month=0,
        price_usd_monthly=0,
    )
    balance = CreditBalance(workspace_id=workspace.id, balance_credits=0)
    db_session.add_all([plan, balance])
    await db_session.flush()
    return plan


class TestPreflightCheck:
    @pytest.mark.asyncio
    async def test_unlimited_allowed(self, db_session, workspace, unlimited_plan):
        from app.billing import repository

        verdict = await repository.preflight_check(db_session, workspace.id)
        assert verdict.allowed is True
        assert verdict.plan == "unlimited"

    @pytest.mark.asyncio
    async def test_zero_balance_blocks(self, db_session, workspace, free_plan):
        from app.billing import repository
        from app.db.models import CreditBalance

        balance = await db_session.get(CreditBalance, workspace.id)
        balance.balance_credits = 0
        await db_session.flush()

        verdict = await repository.preflight_check(db_session, workspace.id)
        assert verdict.allowed is False
        assert verdict.reason == "zero_balance"
        assert verdict.plan == "free"

    @pytest.mark.asyncio
    async def test_positive_balance_allows(self, db_session, workspace, free_plan):
        from app.billing import repository

        verdict = await repository.preflight_check(db_session, workspace.id)
        assert verdict.allowed is True
        assert verdict.balance_credits == 10_000

    @pytest.mark.asyncio
    async def test_no_plan_row_allows_and_warns(self, db_session, workspace):
        # No plan row -> pre-flight defaults to allow + reason='no_plan'.
        # Bug-tolerant: never block a customer because of a self-inflicted
        # bootstrap miss.
        from app.billing import repository

        verdict = await repository.preflight_check(db_session, workspace.id)
        assert verdict.allowed is True
        assert verdict.reason == "no_plan"


class TestDebitForAgentRun:
    @pytest.mark.asyncio
    async def test_subtracts_credits_and_appends_ledger(
        self, db_session, workspace, free_plan
    ):
        from sqlalchemy import select

        from app.billing import repository
        from app.db.models import CreditLedger

        agent_run_id = uuid.uuid4()
        balance = await repository.debit_for_agent_run(
            db_session,
            workspace_id=workspace.id,
            agent_run_id=agent_run_id,
            sales_cost_usd=0.05,
            credits_to_debit=50.0,
        )
        await db_session.flush()

        assert balance is not None
        assert float(balance.balance_credits) == 9_950.0

        ledger_rows = (
            await db_session.execute(
                select(CreditLedger).where(
                    CreditLedger.workspace_id == workspace.id,
                    CreditLedger.kind == "debit_agent_run",
                )
            )
        ).scalars().all()
        assert len(ledger_rows) == 1
        assert float(ledger_rows[0].delta_credits) == -50.0
        assert ledger_rows[0].agent_run_id == agent_run_id
        assert float(ledger_rows[0].balance_after_credits) == 9_950.0

    @pytest.mark.asyncio
    async def test_idempotent_on_agent_run_id(
        self, db_session, workspace, free_plan
    ):
        from sqlalchemy import select

        from app.billing import repository
        from app.db.models import CreditLedger

        agent_run_id = uuid.uuid4()
        await repository.debit_for_agent_run(
            db_session,
            workspace_id=workspace.id,
            agent_run_id=agent_run_id,
            sales_cost_usd=0.05,
            credits_to_debit=50.0,
        )
        await db_session.flush()
        # Second call must not double-debit.
        await repository.debit_for_agent_run(
            db_session,
            workspace_id=workspace.id,
            agent_run_id=agent_run_id,
            sales_cost_usd=0.05,
            credits_to_debit=50.0,
        )
        await db_session.flush()

        ledger_count = (
            await db_session.execute(
                select(CreditLedger).where(
                    CreditLedger.workspace_id == workspace.id,
                    CreditLedger.agent_run_id == agent_run_id,
                )
            )
        ).scalars().all()
        assert len(ledger_count) == 1

    @pytest.mark.asyncio
    async def test_unlimited_plan_skipped(
        self, db_session, workspace, unlimited_plan
    ):
        from sqlalchemy import select

        from app.billing import repository
        from app.db.models import CreditLedger

        result = await repository.debit_for_agent_run(
            db_session,
            workspace_id=workspace.id,
            agent_run_id=uuid.uuid4(),
            sales_cost_usd=999.0,
            credits_to_debit=999_000.0,
        )
        await db_session.flush()
        # Skipped -> returns None, no ledger entry.
        assert result is None
        rows = (
            await db_session.execute(
                select(CreditLedger).where(
                    CreditLedger.workspace_id == workspace.id,
                )
            )
        ).scalars().all()
        assert rows == []

    @pytest.mark.asyncio
    async def test_negative_balance_allowed(
        self, db_session, workspace, free_plan
    ):
        # A single run is allowed to push balance below zero; next
        # pre-flight blocks. Bounded overage = simpler invariant.
        from app.billing import repository

        balance = await repository.debit_for_agent_run(
            db_session,
            workspace_id=workspace.id,
            agent_run_id=uuid.uuid4(),
            sales_cost_usd=15.0,
            credits_to_debit=15_000.0,
        )
        await db_session.flush()
        assert float(balance.balance_credits) == -5_000.0


class TestMonthlyReset:
    @pytest.mark.asyncio
    async def test_wipe_and_grant(self, db_session, workspace, free_plan):
        from app.billing import repository

        # Burn most of the balance first, then reset.
        await repository.debit_for_agent_run(
            db_session,
            workspace_id=workspace.id,
            agent_run_id=uuid.uuid4(),
            sales_cost_usd=5.0,
            credits_to_debit=5_000.0,
        )
        await db_session.flush()

        balance = await repository.credit_monthly_reset(
            db_session,
            workspace_id=workspace.id,
            credits_to_grant=50_000,
        )
        await db_session.flush()
        # Reset is wipe-and-grant: balance is now exactly 50,000, not
        # 50,000 + leftover.
        assert float(balance.balance_credits) == 50_000.0
        assert balance.last_reset_at is not None
