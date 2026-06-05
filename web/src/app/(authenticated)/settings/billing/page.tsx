"use client";

// Settings -> Billing. Shows current plan + balance, the four paid tiers
// as upgrade cards, the recent credit ledger, and entry points into
// Stripe (Checkout for upgrade, Customer Portal for management).
//
// Auth: useAuth().getToken() returns the Clerk JWT we forward to the
// backend. Workspace context comes from the URL or the WorkspaceProvider
// already wrapping the (authenticated) layout.
//
// Stripe-not-configured state: the `stripe_configured` flag from
// /overview drives whether we render upgrade CTAs at all. With keys
// missing we still render the read-only plan + balance card (which
// works for the 'unlimited' tenants like Simetrik).

import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { CreditCard, ExternalLink, Lock, RefreshCw, Sparkles } from "lucide-react";

import { useUserRole } from "@/lib/hooks/useUserRole";

import { PageBody, PageHeader } from "../../_components/page-header";
import {
  billingApi,
  type BillingCycle,
  type BillingOverview,
  type LedgerEntry,
  type PlanOption,
} from "@/lib/api/billing";

const NUMBER_FORMAT = new Intl.NumberFormat("en-US");
const USD_FORMAT = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

function formatCredits(n: number): string {
  return NUMBER_FORMAT.format(Math.max(0, Math.round(n)));
}

function planAccent(plan: string): { surface: string; accent: string } {
  // Coarse color map so the four cards have visual hierarchy without
  // depending on the design system. Keep monochrome-friendly.
  switch (plan) {
    case "starter":
      return { surface: "bg-neutral-50", accent: "text-neutral-700" };
    case "pro":
      return { surface: "bg-blue-50", accent: "text-blue-700" };
    case "scale":
      return { surface: "bg-violet-50", accent: "text-violet-700" };
    case "business":
      return { surface: "bg-amber-50", accent: "text-amber-700" };
    default:
      return { surface: "bg-neutral-50", accent: "text-neutral-700" };
  }
}

export default function SettingsBillingPage() {
  const { getToken } = useAuth();
  const { isAdmin, isLoaded: roleLoaded } = useUserRole();
  const [cycle, setCycle] = useState<BillingCycle>("monthly");
  const [busyPlan, setBusyPlan] = useState<string | null>(null);
  const [portalBusy, setPortalBusy] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  // Treat 'not yet loaded' as admin to avoid flashing the lock state on
  // first paint for actual admins. Backend enforces the gate regardless.
  const billingLocked = roleLoaded && !isAdmin;

  const overviewQuery = useQuery<BillingOverview>({
    queryKey: ["billing", "overview"],
    queryFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk token");
      return billingApi.overview(token);
    },
  });

  const ledgerQuery = useQuery<LedgerEntry[]>({
    queryKey: ["billing", "ledger"],
    queryFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk token");
      return billingApi.ledger(token, null, 20);
    },
  });

  // Surface a one-line message after returning from Checkout (the
  // Stripe success_url comes back with ?checkout=success/cancel).
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const result = params.get("checkout");
    if (result === "success") {
      setErrorMsg(null);
      void overviewQuery.refetch();
      void ledgerQuery.refetch();
    } else if (result === "cancel") {
      setErrorMsg("You cancelled checkout. No charge was made.");
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const overview = overviewQuery.data;

  async function startCheckout(plan: PlanOption) {
    if (billingLocked) {
      setErrorMsg("Only workspace admins can change the plan. Ask your admin.");
      return;
    }
    setErrorMsg(null);
    setBusyPlan(plan.name);
    try {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk token");
      const { url } = await billingApi.checkout(plan.name, cycle, token);
      window.location.href = url;
    } catch (err) {
      setErrorMsg(
        err instanceof Error
          ? err.message
          : "We couldn't start checkout. Try again.",
      );
      setBusyPlan(null);
    }
  }

  async function openPortal() {
    if (billingLocked) {
      setErrorMsg("Only workspace admins can manage billing in Stripe.");
      return;
    }
    setErrorMsg(null);
    setPortalBusy(true);
    try {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk token");
      const { url } = await billingApi.portal(token);
      window.location.href = url;
    } catch (err) {
      setErrorMsg(
        err instanceof Error
          ? err.message
          : "We couldn't open the Stripe portal.",
      );
      setPortalBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Billing"
        Icon={({ className }) => <CreditCard className={className} strokeWidth={1.75} />}
      />
      <PageBody>
        {overviewQuery.isLoading && <LoadingCard />}

        {overviewQuery.isError && (
          <ErrorCard message={(overviewQuery.error as Error)?.message ?? "Error"} />
        )}

        {overview && (
          <>
            {billingLocked && (
              <div className="mb-4 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                <Lock className="mt-0.5 size-3.5 shrink-0" strokeWidth={1.75} />
                <div>
                  <p className="font-medium">Billing is admin-only.</p>
                  <p className="mt-0.5">
                    Only workspace admins can change the plan or open the
                    Stripe portal. Ask an admin to make the change, or to
                    promote you in <span className="font-medium">Settings &rarr; Team</span>.
                  </p>
                </div>
              </div>
            )}
            <CurrentPlanCard
              overview={overview}
              onOpenPortal={openPortal}
              portalBusy={portalBusy}
              billingLocked={billingLocked}
            />

            {errorMsg && (
              <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                {errorMsg}
              </div>
            )}

            {!overview.is_unlimited && (
              <>
                <div id="planes" className="mt-8 flex items-center justify-between scroll-mt-20">
                  <h2 className="text-sm font-medium text-[var(--color-ink-deep)]">
                    Choose your plan
                  </h2>
                  <CycleToggle value={cycle} onChange={setCycle} />
                </div>

                {!overview.stripe_configured && (
                  <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                    Billing is not configured in this environment. Upgrade
                    options are disabled.
                  </div>
                )}

                <CreditSliderPicker
                  cycle={cycle}
                  availablePlans={overview.available_plans}
                  currentPlan={overview.plan}
                  stripeConfigured={overview.stripe_configured}
                  billingLocked={billingLocked}
                  busy={busyPlan}
                  onSelect={(plan) => startCheckout(plan)}
                />
              </>
            )}

          </>
        )}
      </PageBody>
    </>
  );
}


function CurrentPlanCard({
  overview,
  onOpenPortal,
  portalBusy,
  billingLocked,
}: {
  overview: BillingOverview;
  onOpenPortal: () => void;
  portalBusy: boolean;
  billingLocked: boolean;
}) {
  const cyclePart = overview.billing_cycle ? ` (${overview.billing_cycle})` : "";
  const accent = planAccent(overview.plan);
  const percentRemaining =
    overview.credits_per_month > 0
      ? Math.min(
          100,
          Math.max(0, (overview.balance_credits / overview.credits_per_month) * 100),
        )
      : 0;

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-white p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-wider text-neutral-500">
            Current plan
          </p>
          <p className={`mt-1 text-xl font-semibold ${accent.accent}`}>
            {overview.plan_display_name}
            {cyclePart}
          </p>
          {overview.is_unlimited && (
            <p className="mt-1 text-xs text-neutral-500">
              Internal plan without credit metering.
            </p>
          )}
          {!overview.is_unlimited && (
            <p className="mt-1 text-xs text-neutral-500">
              {overview.plan === "free"
                ? "50,000 credits/month. Auto-renews monthly."
                : `${formatCredits(overview.credits_per_month)} credits/month at ${USD_FORMAT.format(overview.price_usd_monthly)}/mo`}
            </p>
          )}
        </div>
        {overview.has_active_subscription && (
          <button
            onClick={onOpenPortal}
            disabled={portalBusy || billingLocked}
            title={billingLocked ? "Admin only" : undefined}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-xs font-medium text-[var(--color-ink-deep)] hover:bg-neutral-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {billingLocked ? (
              <Lock className="h-3.5 w-3.5" strokeWidth={1.75} />
            ) : (
              <ExternalLink className="h-3.5 w-3.5" />
            )}
            {portalBusy ? "Opening…" : "Manage"}
          </button>
        )}
        {!overview.has_active_subscription && !overview.is_unlimited && (
          billingLocked ? (
            <span
              title="Admin only"
              className="inline-flex cursor-not-allowed items-center gap-1.5 rounded-md bg-neutral-200 px-3 py-1.5 text-xs font-medium text-neutral-500"
            >
              <Lock className="h-3.5 w-3.5" strokeWidth={1.75} />
              Upgrade
            </span>
          ) : (
            <a
              href="#planes"
              className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-ink-deep)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
            >
              <Sparkles className="h-3.5 w-3.5" />
              Upgrade
            </a>
          )
        )}
      </div>

      {!overview.is_unlimited && (
        <div className="mt-4">
          <div className="flex items-center justify-between text-xs text-neutral-500">
            <span>Credits available this month</span>
            <span className="font-medium text-[var(--color-ink-deep)]">
              {formatCredits(overview.balance_credits)} / {formatCredits(overview.credits_per_month)}
            </span>
          </div>
          <div className="mt-2 h-2 rounded-full bg-neutral-100">
            <div
              className="h-2 rounded-full bg-[var(--color-ink-deep)]"
              style={{ width: `${percentRemaining}%` }}
            />
          </div>
        </div>
      )}

      {overview.cancel_at_period_end && overview.current_period_end && (
        <p className="mt-3 text-xs text-amber-700">
          Your subscription cancels on{" "}
          {new Date(overview.current_period_end).toLocaleDateString()}.
        </p>
      )}
    </div>
  );
}


function CycleToggle({
  value,
  onChange,
}: {
  value: BillingCycle;
  onChange: (v: BillingCycle) => void;
}) {
  return (
    <div className="inline-flex overflow-hidden rounded-md border border-[var(--color-border)] bg-white">
      <button
        onClick={() => onChange("monthly")}
        className={`px-3 py-1.5 text-xs font-medium ${
          value === "monthly"
            ? "bg-[var(--color-ink-deep)] text-white"
            : "text-neutral-600 hover:bg-neutral-50"
        }`}
      >
        Monthly
      </button>
      <button
        onClick={() => onChange("annual")}
        className={`px-3 py-1.5 text-xs font-medium ${
          value === "annual"
            ? "bg-[var(--color-ink-deep)] text-white"
            : "text-neutral-600 hover:bg-neutral-50"
        }`}
      >
        Annual <span className="opacity-70">(-20%)</span>
      </button>
    </div>
  );
}


// ── Per-tier credit sliders (Krea-style, one slider per tier) ─────────── //

// Each tier owns its own range of (credits, price) discrete points. The
// slider inside a tier card snaps to those points. Tiers stay separate
// cards so the customer compares ranges side by side.
//
// Pricing: $2.50 USD / 1,000 credits across all tiers. With the 5x
// SALES_COST_MULTIPLIER unchanged, real LLM cost is $0.50 / 1k credits
// and gross margin is 80%.
type TierPoint = { credits: number; price: number };
type TierDef = {
  name: string; // matches PlanOption.name (starter / pro / scale / business / enterprise)
  display: string;
  description: string;
  badge?: string;
  popular?: boolean; // adds the "Most popular" tag
  points: TierPoint[];
  // Index inside `points` that the slider lands on when the page first
  // renders. Defaults to 0 (cheapest). For Pro we anchor on the
  // "Most popular" price as the recommended pick.
  defaultIndex: number;
};

const TIER_DEFS: TierDef[] = [
  {
    name: "starter",
    display: "Starter",
    description: "For small teams trying the agent.",
    badge: "Small-sized companies",
    points: [
      { credits: 40_000, price: 100 },
      { credits: 80_000, price: 200 },
      { credits: 125_000, price: 300 },
    ],
    defaultIndex: 0,
  },
  {
    name: "pro",
    display: "Pro",
    description: "For teams automating day-to-day work.",
    popular: true,
    points: [
      { credits: 160_000, price: 400 },
      { credits: 200_000, price: 500 },
      { credits: 300_000, price: 750 },
      { credits: 400_000, price: 1_000 },
    ],
    defaultIndex: 2, // 300k / $750 = "Most popular" anchor
  },
  {
    name: "scale",
    display: "Scale",
    description: "For mid-market companies with multiple workspaces.",
    badge: "Medium-sized companies",
    points: [
      { credits: 600_000, price: 1_500 },
      { credits: 800_000, price: 2_000 },
      { credits: 1_200_000, price: 3_000 },
      { credits: 1_600_000, price: 4_000 },
      { credits: 2_000_000, price: 5_000 },
    ],
    defaultIndex: 0,
  },
  {
    name: "business",
    display: "Business",
    description: "For companies with SLA, SSO, and compliance needs.",
    points: [
      { credits: 3_000_000, price: 7_500 },
      { credits: 4_000_000, price: 10_000 },
      { credits: 5_000_000, price: 12_500 },
      { credits: 6_000_000, price: 15_000 },
      { credits: 8_000_000, price: 20_000 },
      { credits: 10_000_000, price: 25_000 },
    ],
    defaultIndex: 0,
  },
  {
    name: "enterprise",
    display: "Enterprise",
    description: "For high volume with discounts and custom integrations.",
    badge: "Enterprise",
    points: [
      { credits: 12_000_000, price: 30_000 },
      { credits: 14_000_000, price: 35_000 },
      { credits: 16_000_000, price: 40_000 },
      { credits: 18_000_000, price: 45_000 },
      { credits: 20_000_000, price: 50_000 },
    ],
    defaultIndex: 0,
  },
];

// Universal features. Every paid tier gets all five (per user spec
// 2026-06-02). Higher tiers add SSO / RBAC / audit_log / SLA via
// FEATURE_MATRIX server-side but those aren't surfaced in the slider
// cards.
const UNIVERSAL_FEATURES = [
  "Slack-native agent in threads + mentions",
  "Persistent workspace context",
  "Integrations + tool execution",
  "Scheduled tasks & crons (reports, audits, proactive check-ins)",
  "Drafts + artifacts (updates, tickets/docs where supported)",
];


function CreditSliderPicker({
  cycle,
  availablePlans,
  currentPlan,
  stripeConfigured,
  billingLocked,
  busy,
  onSelect,
}: {
  cycle: BillingCycle;
  availablePlans: PlanOption[];
  currentPlan: string;
  stripeConfigured: boolean;
  billingLocked: boolean;
  busy: string | null;
  onSelect: (plan: PlanOption) => void;
}) {
  return (
    <>
      <div className="mt-4 grid gap-3 lg:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
        {TIER_DEFS.map((tier) => (
          <TierSliderCard
            key={tier.name}
            tier={tier}
            cycle={cycle}
            availablePlans={availablePlans}
            currentPlan={currentPlan}
            stripeConfigured={stripeConfigured}
            billingLocked={billingLocked}
            busy={busy}
            onSelect={onSelect}
          />
        ))}
      </div>
    </>
  );
}


function TierSliderCard({
  tier,
  cycle,
  availablePlans,
  currentPlan,
  stripeConfigured,
  billingLocked,
  busy,
  onSelect,
}: {
  tier: TierDef;
  cycle: BillingCycle;
  availablePlans: PlanOption[];
  currentPlan: string;
  stripeConfigured: boolean;
  billingLocked: boolean;
  busy: string | null;
  onSelect: (plan: PlanOption) => void;
}) {
  const [index, setIndex] = useState<number>(tier.defaultIndex);
  const point = tier.points[index];
  const monthlyPrice = cycle === "annual" ? point.price * 0.8 : point.price;
  const annualTotal = point.price * 12 * 0.8;

  // Map this tier to its backend-served PlanOption so checkout uses the
  // matching Stripe price_id. The price displayed on the slider may
  // differ from the tier's floor; the backend currently bills the floor
  // (next slice: per-quantity Stripe metering).
  const plan = availablePlans.find((p) => p.name === tier.name);
  const isCurrent = currentPlan === tier.name;
  const isBusy = !!busy && busy === tier.name;
  const checkoutAvailable =
    !!plan &&
    stripeConfigured &&
    (cycle === "monthly" ? plan.has_monthly_checkout : plan.has_annual_checkout);
  const disabled = !checkoutAvailable || isBusy || isCurrent || !plan || billingLocked;
  const isEnterprise = tier.name === "enterprise";

  const tagClass = tier.popular
    ? "bg-orange-500 text-white"
    : "bg-neutral-100 text-neutral-700";

  const cardBorder = tier.popular
    ? "border-orange-500 shadow-[0_2px_0_0_rgba(255,82,0,0.4)]"
    : "border-[var(--color-border)]";

  return (
    <div className={`flex flex-col rounded-xl border ${cardBorder} bg-white p-4`}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-[var(--color-ink-deep)]">
          {tier.display}
        </h3>
        {tier.popular && (
          <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${tagClass}`}>
            Most popular
          </span>
        )}
        {!tier.popular && tier.badge && (
          <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${tagClass}`}>
            {tier.badge}
          </span>
        )}
      </div>

      <p className="mt-1 text-xs text-neutral-500">{tier.description}</p>

      <div className="mt-4">
        <p className="text-2xl font-semibold text-[var(--color-ink-deep)]">
          {USD_FORMAT.format(Math.round(monthlyPrice))}
          <span className="ml-1 text-xs font-normal text-neutral-500">/mo</span>
        </p>
        {cycle === "annual" && (
          <p className="text-[11px] text-neutral-500">
            {USD_FORMAT.format(Math.round(annualTotal))} per year
          </p>
        )}
        <p className="mt-0.5 text-xs font-medium text-[var(--color-ink-deep)]">
          {formatCredits(point.credits)} credits / month
        </p>
      </div>

      <div className="mt-4">
        <input
          type="range"
          min={0}
          max={tier.points.length - 1}
          step={1}
          value={index}
          onChange={(e) => setIndex(Number(e.target.value))}
          aria-label={`Credits per month for ${tier.display}`}
          className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-neutral-200 accent-[var(--color-ink-deep)]"
        />
        <div className="mt-1 flex justify-between text-[10px] text-neutral-400">
          <span>{formatCredits(tier.points[0].credits)}</span>
          <span>{formatCredits(tier.points[tier.points.length - 1].credits)}</span>
        </div>
      </div>

      <button
        onClick={() => plan && onSelect(plan)}
        disabled={disabled}
        title={billingLocked ? "Admin only" : undefined}
        className={`mt-auto pt-4`}
      >
        <span
          className={`inline-flex w-full items-center justify-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium ${
            isCurrent
              ? "bg-neutral-200 text-neutral-600"
              : tier.popular
                ? "bg-orange-500 text-white hover:bg-orange-600"
                : "bg-[var(--color-ink-deep)] text-white hover:opacity-90"
          } disabled:cursor-not-allowed disabled:opacity-40`}
        >
          {isCurrent
            ? "Current plan"
            : billingLocked
              ? (
                <>
                  <Lock className="h-3.5 w-3.5" strokeWidth={1.75} />
                  Admin only
                </>
              )
              : isBusy
                ? "Redirecting…"
                : isEnterprise
                  ? "Talk to sales"
                  : (
                    <>
                      <Sparkles className="h-3.5 w-3.5" />
                      Get {tier.display}
                    </>
                  )}
        </span>
      </button>

    </div>
  );
}


function PlanCard({
  plan,
  cycle,
  currentPlan,
  stripeConfigured,
  busy,
  onSelect,
}: {
  plan: PlanOption;
  cycle: BillingCycle;
  currentPlan: string;
  stripeConfigured: boolean;
  busy: boolean;
  onSelect: () => void;
}) {
  const accent = planAccent(plan.name);
  const isCurrent = currentPlan === plan.name;
  const checkoutAvailable =
    cycle === "monthly" ? plan.has_monthly_checkout : plan.has_annual_checkout;
  const disabled = !stripeConfigured || !checkoutAvailable || busy || isCurrent;

  const monthlyEffective =
    cycle === "monthly"
      ? plan.monthly_price_floor
      : plan.annual_price_floor / 12;

  return (
    <div
      className={`flex flex-col rounded-xl border border-[var(--color-border)] ${accent.surface} p-4`}
    >
      <div>
        <p className={`text-sm font-semibold ${accent.accent}`}>
          {plan.display_name}
        </p>
        <p className="mt-1 text-xs text-neutral-600">{plan.description}</p>
      </div>
      <div className="mt-3">
        <p className="text-2xl font-semibold text-[var(--color-ink-deep)]">
          {USD_FORMAT.format(monthlyEffective)}
          <span className="ml-1 text-xs font-normal text-neutral-500">/mo</span>
        </p>
        {cycle === "annual" && (
          <p className="text-xs text-neutral-500">
            {USD_FORMAT.format(plan.annual_price_floor)} per year
          </p>
        )}
        <p className="mt-1 text-xs text-neutral-500">
          starting from {formatCredits(plan.credits_floor)} credits/month
        </p>
      </div>
      <button
        onClick={onSelect}
        disabled={disabled}
        className="mt-4 inline-flex items-center justify-center gap-1.5 rounded-md bg-[var(--color-ink-deep)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {isCurrent ? "Current plan" : busy ? "Redirecting…" : (
          <>
            <Sparkles className="h-3.5 w-3.5" />
            Select
          </>
        )}
      </button>
    </div>
  );
}


function LedgerSection({
  entries,
  loading,
  onRefresh,
}: {
  entries: LedgerEntry[];
  loading: boolean;
  onRefresh: () => void;
}) {
  return (
    <div className="mt-8">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-[var(--color-ink-deep)]">
          Recent activity
        </h2>
        <button
          onClick={onRefresh}
          className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-white px-2.5 py-1 text-xs text-neutral-600 hover:bg-neutral-50"
        >
          <RefreshCw className="h-3 w-3" />
          Refresh
        </button>
      </div>
      <div className="mt-3 overflow-hidden rounded-xl border border-[var(--color-border)] bg-white">
        {loading && (
          <div className="p-4 text-xs text-neutral-500">Loading…</div>
        )}
        {!loading && entries.length === 0 && (
          <div className="p-6 text-center text-xs text-neutral-500">
            No activity yet.
          </div>
        )}
        {!loading && entries.length > 0 && (
          <table className="w-full text-xs">
            <thead className="border-b border-[var(--color-border)] bg-neutral-50 text-neutral-500">
              <tr>
                <th className="px-3 py-2 text-left font-medium">When</th>
                <th className="px-3 py-2 text-left font-medium">Type</th>
                <th className="px-3 py-2 text-right font-medium">Change</th>
                <th className="px-3 py-2 text-right font-medium">Balance</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id} className="border-b border-[var(--color-border)] last:border-0">
                  <td className="px-3 py-2 text-neutral-600">
                    {formatDistanceToNow(new Date(e.created_at), { addSuffix: true })}
                  </td>
                  <td className="px-3 py-2 text-neutral-600">{e.kind.replace(/_/g, " ")}</td>
                  <td
                    className={`px-3 py-2 text-right font-medium ${
                      e.delta_credits < 0 ? "text-red-600" : "text-emerald-700"
                    }`}
                  >
                    {e.delta_credits > 0 ? "+" : ""}
                    {formatCredits(Math.abs(e.delta_credits))}
                  </td>
                  <td className="px-3 py-2 text-right text-neutral-800">
                    {formatCredits(e.balance_after_credits)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}


function LoadingCard() {
  return (
    <div className="animate-pulse rounded-xl border border-[var(--color-border)] bg-white p-5">
      <div className="h-3 w-20 rounded bg-neutral-100" />
      <div className="mt-2 h-6 w-40 rounded bg-neutral-100" />
      <div className="mt-3 h-3 w-64 rounded bg-neutral-100" />
    </div>
  );
}


function ErrorCard({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
      We couldn't load your billing: {message}
    </div>
  );
}
