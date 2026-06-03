"""Plan taxonomy + credit math. Single source of truth for tier names,
slider ranges, and feature gates.

Pricing decided 2026-06-02:
  - 1 credit = $0.001 USD sales price (CREDITS_PER_USD = 1000)
  - 5x SALES_COST_MULTIPLIER means real LLM cost is $0.0002 / credit
  - Free tier: $50/mo sales value (50k credits), $10/mo real cost.
    Perpetuo. Reset monthly.
  - Paid tiers use a Krea-style slider: pick a price within the tier's
    range and credits scale linearly at $1 sale = 1,000 credits.
  - Annual: 20% off, paid upfront, credits reset monthly inside the
    12-month window.

Features per tier are listed in `FEATURE_MATRIX` below. The runner
doesn't enforce features in Slice 1; this constant exists so the
pricing page and (future) feature-gate decorator can read from one
place.
"""

from __future__ import annotations

from dataclasses import dataclass


# Sentinel: customer is a pre-billing tenant (Simetrik, Antiff, diio,
# Supersonik). Pre-flight skips the balance check and the runner skips
# the debit. Filled in by migration 0031 backfill.
PLAN_UNLIMITED = "unlimited"
PLAN_FREE = "free"
PLAN_STARTER = "starter"
PLAN_PRO = "pro"
PLAN_SCALE = "scale"
PLAN_BUSINESS = "business"
PLAN_ENTERPRISE = "enterprise"

ALL_PLANS = (
    PLAN_FREE, PLAN_STARTER, PLAN_PRO, PLAN_SCALE,
    PLAN_BUSINESS, PLAN_ENTERPRISE, PLAN_UNLIMITED,
)

# Plans where credit checks are bypassed entirely. Enterprise is in here
# because their billing is contract-side, not metered by Stripe in MVP.
BYPASS_CREDIT_CHECK = frozenset({PLAN_UNLIMITED, PLAN_ENTERPRISE})

CREDITS_PER_USD = 1000.0  # mirrors app.config.CREDITS_PER_USD


@dataclass(frozen=True)
class PlanSpec:
    """Static info for one tier. The slider lets the customer pick a
    price within [`price_floor`, `price_ceiling`]; credits scale at the
    1 credit = $0.001 ratio for both ends."""

    name: str
    display_name: str
    price_floor: float          # monthly USD at the floor of the slider
    price_ceiling: float        # monthly USD at the ceiling
    description: str

    @property
    def credits_floor(self) -> int:
        return int(self.price_floor * CREDITS_PER_USD)

    @property
    def credits_ceiling(self) -> int:
        return int(self.price_ceiling * CREDITS_PER_USD)


# Source of truth for the tier ladder. Free is perpetual at 50k credits;
# paid tiers cover non-overlapping price ranges with features that
# strictly grow upward. Enterprise has no fixed slider -- contracts only.
PLANS: dict[str, PlanSpec] = {
    PLAN_FREE: PlanSpec(
        name=PLAN_FREE,
        display_name="Free",
        price_floor=0.0,
        price_ceiling=0.0,
        description="50,000 credits/mo. Perpetuo. 3 integraciones activas.",
    ),
    PLAN_STARTER: PlanSpec(
        name=PLAN_STARTER,
        display_name="Starter",
        price_floor=100.0,
        price_ceiling=300.0,
        description="100k-300k credits/mo. Integraciones ilimitadas. Soporte email.",
    ),
    PLAN_PRO: PlanSpec(
        name=PLAN_PRO,
        display_name="Pro",
        price_floor=400.0,
        price_ceiling=1_000.0,
        description="400k-1M credits/mo. Custom skills, workspace analytics, API.",
    ),
    PLAN_SCALE: PlanSpec(
        name=PLAN_SCALE,
        display_name="Scale",
        price_floor=1_500.0,
        price_ceiling=3_000.0,
        description="1.5M-3M credits/mo. Multi-workspace, Slack support, onboarding.",
    ),
    PLAN_BUSINESS: PlanSpec(
        name=PLAN_BUSINESS,
        display_name="Business",
        price_floor=5_000.0,
        price_ceiling=10_000.0,
        description="5M-10M credits/mo. SSO, audit log, RBAC, SLA 99.9%, CSM.",
    ),
}

# 50,000 credits/mo, fixed (no slider) for free tier.
FREE_TIER_CREDITS_PER_MONTH = 50_000

# Annual discount: 20% off the monthly price, paid upfront.
ANNUAL_DISCOUNT = 0.20


# Feature flags per tier. Not enforced by the runner in Slice 1; the
# (future) feature-gate decorator reads this when locking a tool / page
# behind a tier. The matrix is monotonic: every feature in tier N is
# also in tier N+1.
FEATURE_MATRIX: dict[str, set[str]] = {
    PLAN_FREE: {
        "basic_skills",
        "integrations_limited_3",
        "automations_limited_5",
        "scheduled_tasks_limited_10",
        "support_community",
    },
    PLAN_STARTER: {
        "basic_skills",
        "integrations_unlimited",
        "automations_unlimited",
        "scheduled_tasks_unlimited",
        "support_email",
    },
    PLAN_PRO: {
        "basic_skills",
        "integrations_unlimited",
        "automations_unlimited",
        "scheduled_tasks_unlimited",
        "custom_skills_builder",
        "workspace_analytics",
        "api_access",
        "support_email",
        "support_priority",
    },
    PLAN_SCALE: {
        "basic_skills",
        "integrations_unlimited",
        "automations_unlimited",
        "scheduled_tasks_unlimited",
        "custom_skills_builder",
        "workspace_analytics",
        "api_access",
        "support_email",
        "support_priority",
        "multi_workspace",
        "support_slack",
        "dedicated_onboarding",
    },
    PLAN_BUSINESS: {
        "basic_skills",
        "integrations_unlimited",
        "automations_unlimited",
        "scheduled_tasks_unlimited",
        "custom_skills_builder",
        "workspace_analytics",
        "api_access",
        "support_email",
        "support_priority",
        "multi_workspace",
        "support_slack",
        "dedicated_onboarding",
        "sso",
        "audit_log",
        "rbac",
        "sla_99_9",
        "csm",
    },
    PLAN_ENTERPRISE: {  # superset; custom contracts extend this further
        "basic_skills",
        "integrations_unlimited",
        "automations_unlimited",
        "scheduled_tasks_unlimited",
        "custom_skills_builder",
        "workspace_analytics",
        "api_access",
        "support_email",
        "support_priority",
        "multi_workspace",
        "support_slack",
        "dedicated_onboarding",
        "sso",
        "audit_log",
        "rbac",
        "sla_99_9",
        "sla_99_99",
        "csm",
        "custom_integrations",
        "volume_discount",
        "custom_retention",
    },
    PLAN_UNLIMITED: {  # sentinel: behaves like enterprise for feature checks
        "basic_skills",
        "integrations_unlimited",
        "automations_unlimited",
        "scheduled_tasks_unlimited",
        "custom_skills_builder",
        "workspace_analytics",
        "api_access",
        "support_email",
        "support_priority",
        "multi_workspace",
        "support_slack",
        "dedicated_onboarding",
        "sso",
        "audit_log",
        "rbac",
        "sla_99_9",
        "sla_99_99",
        "csm",
        "custom_integrations",
        "volume_discount",
        "custom_retention",
    },
}


def has_feature(plan: str, feature: str) -> bool:
    """Returns True if the given plan unlocks the given feature. Unknown
    plans (corrupt DB row, race during plan change) default to free."""
    return feature in FEATURE_MATRIX.get(plan, FEATURE_MATRIX[PLAN_FREE])


def annual_price(monthly_price: float) -> float:
    """Yearly upfront price for a plan, after the 20% discount."""
    return monthly_price * 12 * (1 - ANNUAL_DISCOUNT)


def credits_for_price(monthly_price: float) -> int:
    """How many credits a customer gets for a given monthly price.
    Linear: $1 = 1,000 credits. Same ratio across all tiers."""
    return int(monthly_price * CREDITS_PER_USD)
