"""Pure-Python unit tests for the plan taxonomy + math helpers.

Pins the credit-to-USD ratio, the slider boundaries per tier, and the
feature matrix's monotonicity (every feature in tier N must be in N+1)
so a refactor that scrambles the ladder fails loudly here instead of
in production billing."""

from __future__ import annotations

import pytest

from app.billing import plans


class TestCreditMath:
    def test_credits_per_usd_is_thousand(self):
        # 1 credit = $0.001 USD sales. Foundational invariant the
        # debit logic + Stripe pricing both depend on.
        assert plans.CREDITS_PER_USD == 1000.0

    def test_free_tier_credits_match_spec(self):
        # $50/mo in credits = 50,000 credits.
        assert plans.FREE_TIER_CREDITS_PER_MONTH == 50_000

    @pytest.mark.parametrize(
        "monthly_price, expected_credits",
        [
            (100, 100_000),
            (300, 300_000),
            (1_000, 1_000_000),
            (5_000, 5_000_000),
        ],
    )
    def test_credits_for_price_linear(self, monthly_price, expected_credits):
        assert plans.credits_for_price(monthly_price) == expected_credits

    def test_annual_discount_is_20_percent(self):
        # $100/mo monthly = $1,200/year before discount.
        # 20% off = $960/year.
        assert plans.annual_price(100) == pytest.approx(960.0)

    def test_annual_discount_constant(self):
        assert plans.ANNUAL_DISCOUNT == 0.20


class TestPlanLadder:
    """Boundaries between tiers should never overlap or leave gaps."""

    def test_all_tiers_have_specs(self):
        for tier in (
            plans.PLAN_FREE, plans.PLAN_STARTER, plans.PLAN_PRO,
            plans.PLAN_SCALE, plans.PLAN_BUSINESS,
        ):
            assert tier in plans.PLANS

    def test_starter_range(self):
        spec = plans.PLANS[plans.PLAN_STARTER]
        assert spec.price_floor == 100.0
        assert spec.price_ceiling == 300.0
        assert spec.credits_floor == 100_000
        assert spec.credits_ceiling == 300_000

    def test_pro_range(self):
        spec = plans.PLANS[plans.PLAN_PRO]
        assert spec.price_floor == 400.0
        assert spec.price_ceiling == 1_000.0

    def test_scale_range(self):
        spec = plans.PLANS[plans.PLAN_SCALE]
        assert spec.price_floor == 1_500.0
        assert spec.price_ceiling == 3_000.0

    def test_business_range(self):
        spec = plans.PLANS[plans.PLAN_BUSINESS]
        assert spec.price_floor == 5_000.0
        assert spec.price_ceiling == 10_000.0

    def test_no_overlapping_tiers(self):
        # Starter ceiling must be <= Pro floor, Pro ceiling <= Scale
        # floor, etc. A gap is fine (intentional: the slider is
        # discontinuous between tiers); overlap would let two tiers
        # serve the same price point.
        ladder = [
            plans.PLAN_STARTER, plans.PLAN_PRO,
            plans.PLAN_SCALE, plans.PLAN_BUSINESS,
        ]
        for lower, upper in zip(ladder, ladder[1:]):
            assert plans.PLANS[lower].price_ceiling <= plans.PLANS[upper].price_floor, (
                f"Tier overlap: {lower}.ceiling > {upper}.floor"
            )


class TestFeatureMatrix:
    def test_every_plan_has_features(self):
        for plan in plans.ALL_PLANS:
            assert plan in plans.FEATURE_MATRIX
            assert plans.FEATURE_MATRIX[plan], f"empty feature set for {plan}"

    def test_paid_tiers_strictly_increasing(self):
        # Free -> Starter -> Pro -> Scale -> Business: each must include
        # every feature of the previous tier. Catches accidental regressions
        # where reorganizing the matrix drops a feature higher up.
        # (Note: free -> starter swaps 'integrations_limited_3' for
        # 'integrations_unlimited', so we compare paid-only.)
        ladder = [
            plans.PLAN_STARTER, plans.PLAN_PRO,
            plans.PLAN_SCALE, plans.PLAN_BUSINESS,
        ]
        for lower, upper in zip(ladder, ladder[1:]):
            lower_features = plans.FEATURE_MATRIX[lower]
            upper_features = plans.FEATURE_MATRIX[upper]
            assert lower_features.issubset(upper_features), (
                f"{upper} missing features from {lower}: "
                f"{lower_features - upper_features}"
            )

    def test_business_has_enterprise_blockers(self):
        # The features that distinguish Business from Pro are the
        # ones enterprise customers ask for. Pin them so they don't
        # silently downgrade into a cheaper tier.
        biz = plans.FEATURE_MATRIX[plans.PLAN_BUSINESS]
        for f in ("sso", "audit_log", "rbac", "sla_99_9", "csm"):
            assert f in biz, f"Business missing enterprise-blocker feature: {f}"

    def test_unlimited_is_superset(self):
        # Unlimited is the bypass sentinel for pre-billing tenants
        # (Simetrik etc). It MUST behave like enterprise for any
        # feature-gate check, otherwise migrating to a real plan
        # later would feel like a feature downgrade.
        ent = plans.FEATURE_MATRIX[plans.PLAN_ENTERPRISE]
        unl = plans.FEATURE_MATRIX[plans.PLAN_UNLIMITED]
        assert ent.issubset(unl)


class TestBypassCheck:
    def test_unlimited_bypasses(self):
        assert plans.PLAN_UNLIMITED in plans.BYPASS_CREDIT_CHECK

    def test_enterprise_bypasses(self):
        # Enterprise is contract-billed in MVP; metered checks would
        # block deals where billing happens outside Stripe.
        assert plans.PLAN_ENTERPRISE in plans.BYPASS_CREDIT_CHECK

    def test_free_does_not_bypass(self):
        # Free has a metered ceiling. Skipping the check here would
        # be a permanent revenue leak.
        assert plans.PLAN_FREE not in plans.BYPASS_CREDIT_CHECK

    def test_paid_tiers_do_not_bypass(self):
        for tier in (
            plans.PLAN_STARTER, plans.PLAN_PRO,
            plans.PLAN_SCALE, plans.PLAN_BUSINESS,
        ):
            assert tier not in plans.BYPASS_CREDIT_CHECK


class TestHasFeature:
    def test_known_plan_known_feature(self):
        assert plans.has_feature(plans.PLAN_BUSINESS, "sso") is True
        assert plans.has_feature(plans.PLAN_STARTER, "sso") is False

    def test_unknown_plan_defaults_to_free(self):
        # Corrupt DB row / mid-rollout race. Don't grant accidental access.
        assert plans.has_feature("not_a_real_plan", "sso") is False
        assert plans.has_feature("not_a_real_plan", "basic_skills") is True


class TestSlackMessage:
    """The hard-stop Slack message must always render with the right
    URL and a button so the customer's escape path is one click."""

    def test_renders_with_billing_url(self, monkeypatch):
        from app.billing import slack_messages

        # Stub the settings so the test doesn't depend on env config.
        class _Settings:
            web_base_url = "https://example.com"

        monkeypatch.setattr(
            slack_messages, "get_settings", lambda: _Settings()
        )
        payload = slack_messages.out_of_credits_message("starter", 0)
        assert payload["text"]
        assert any(
            el.get("url") == "https://example.com/settings/billing"
            for block in payload["blocks"]
            if block["type"] == "actions"
            for el in block["elements"]
        )

    def test_free_plan_message_differs_from_paid(self, monkeypatch):
        from app.billing import slack_messages

        class _Settings:
            web_base_url = "https://example.com"

        monkeypatch.setattr(
            slack_messages, "get_settings", lambda: _Settings()
        )
        free = slack_messages.out_of_credits_message("free", 0)["text"]
        starter = slack_messages.out_of_credits_message("starter", 0)["text"]
        # Free tier message nudges upgrade; paid tier message nudges
        # renewal / upgrade-tier. Different audiences, different CTAs.
        assert "Free" in free or "free" in free.lower()
        assert "Starter" in starter or "starter" in starter.lower()
        assert free != starter
