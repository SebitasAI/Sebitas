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
import { CreditCard, ExternalLink, RefreshCw, Sparkles } from "lucide-react";

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
  const [cycle, setCycle] = useState<BillingCycle>("monthly");
  const [busyPlan, setBusyPlan] = useState<string | null>(null);
  const [portalBusy, setPortalBusy] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const overviewQuery = useQuery<BillingOverview>({
    queryKey: ["billing", "overview"],
    queryFn: async () => {
      const token = await getToken();
      if (!token) throw new Error("No Clerk token");
      return billingApi.overview(token);
    },
  });

  const ledgerQuery = useQuery<LedgerEntry[]>({
    queryKey: ["billing", "ledger"],
    queryFn: async () => {
      const token = await getToken();
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
      setErrorMsg("Cancelaste el checkout. No se hizo ningún cargo.");
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const overview = overviewQuery.data;

  async function startCheckout(plan: PlanOption) {
    setErrorMsg(null);
    setBusyPlan(plan.name);
    try {
      const token = await getToken();
      if (!token) throw new Error("No Clerk token");
      const { url } = await billingApi.checkout(plan.name, cycle, token);
      window.location.href = url;
    } catch (err) {
      setErrorMsg(
        err instanceof Error
          ? err.message
          : "No pudimos iniciar el checkout. Intenta de nuevo.",
      );
      setBusyPlan(null);
    }
  }

  async function openPortal() {
    setErrorMsg(null);
    setPortalBusy(true);
    try {
      const token = await getToken();
      if (!token) throw new Error("No Clerk token");
      const { url } = await billingApi.portal(token);
      window.location.href = url;
    } catch (err) {
      setErrorMsg(
        err instanceof Error
          ? err.message
          : "No pudimos abrir el portal de Stripe.",
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
            <CurrentPlanCard
              overview={overview}
              onOpenPortal={openPortal}
              portalBusy={portalBusy}
            />

            {errorMsg && (
              <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                {errorMsg}
              </div>
            )}

            {!overview.is_unlimited && (
              <>
                <div className="mt-8 flex items-center justify-between">
                  <h2 className="text-sm font-medium text-[var(--color-ink-deep)]">
                    Planes
                  </h2>
                  <CycleToggle value={cycle} onChange={setCycle} />
                </div>

                {!overview.stripe_configured && (
                  <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                    Billing no está configurado en este ambiente. Las opciones
                    de upgrade están deshabilitadas.
                  </div>
                )}

                <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                  {overview.available_plans.map((plan) => (
                    <PlanCard
                      key={plan.name}
                      plan={plan}
                      cycle={cycle}
                      currentPlan={overview.plan}
                      stripeConfigured={overview.stripe_configured}
                      busy={busyPlan === plan.name}
                      onSelect={() => startCheckout(plan)}
                    />
                  ))}
                </div>
              </>
            )}

            <LedgerSection
              entries={ledgerQuery.data ?? []}
              loading={ledgerQuery.isLoading}
              onRefresh={() => {
                void overviewQuery.refetch();
                void ledgerQuery.refetch();
              }}
            />
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
}: {
  overview: BillingOverview;
  onOpenPortal: () => void;
  portalBusy: boolean;
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
            Plan actual
          </p>
          <p className={`mt-1 text-xl font-semibold ${accent.accent}`}>
            {overview.plan_display_name}
            {cyclePart}
          </p>
          {overview.is_unlimited && (
            <p className="mt-1 text-xs text-neutral-500">
              Plan interno sin medición de créditos.
            </p>
          )}
          {!overview.is_unlimited && (
            <p className="mt-1 text-xs text-neutral-500">
              {overview.plan === "free"
                ? "50,000 créditos/mes. Renovación automática mensual."
                : `${formatCredits(overview.credits_per_month)} créditos/mes a ${USD_FORMAT.format(overview.price_usd_monthly)}/mes`}
            </p>
          )}
        </div>
        {overview.has_active_subscription && (
          <button
            onClick={onOpenPortal}
            disabled={portalBusy}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-xs font-medium text-[var(--color-ink-deep)] hover:bg-neutral-50 disabled:opacity-50"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            {portalBusy ? "Abriendo…" : "Administrar"}
          </button>
        )}
      </div>

      {!overview.is_unlimited && (
        <div className="mt-4">
          <div className="flex items-center justify-between text-xs text-neutral-500">
            <span>Créditos disponibles este mes</span>
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
          Tu suscripción se cancela el{" "}
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
        Mensual
      </button>
      <button
        onClick={() => onChange("annual")}
        className={`px-3 py-1.5 text-xs font-medium ${
          value === "annual"
            ? "bg-[var(--color-ink-deep)] text-white"
            : "text-neutral-600 hover:bg-neutral-50"
        }`}
      >
        Anual <span className="opacity-70">(-20%)</span>
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
          <span className="ml-1 text-xs font-normal text-neutral-500">/mes</span>
        </p>
        {cycle === "annual" && (
          <p className="text-xs text-neutral-500">
            {USD_FORMAT.format(plan.annual_price_floor)} al año
          </p>
        )}
        <p className="mt-1 text-xs text-neutral-500">
          desde {formatCredits(plan.credits_floor)} créditos/mes
        </p>
      </div>
      <button
        onClick={onSelect}
        disabled={disabled}
        className="mt-4 inline-flex items-center justify-center gap-1.5 rounded-md bg-[var(--color-ink-deep)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {isCurrent ? "Plan actual" : busy ? "Redirigiendo…" : (
          <>
            <Sparkles className="h-3.5 w-3.5" />
            Seleccionar
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
          Movimientos recientes
        </h2>
        <button
          onClick={onRefresh}
          className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-white px-2.5 py-1 text-xs text-neutral-600 hover:bg-neutral-50"
        >
          <RefreshCw className="h-3 w-3" />
          Refrescar
        </button>
      </div>
      <div className="mt-3 overflow-hidden rounded-xl border border-[var(--color-border)] bg-white">
        {loading && (
          <div className="p-4 text-xs text-neutral-500">Cargando…</div>
        )}
        {!loading && entries.length === 0 && (
          <div className="p-6 text-center text-xs text-neutral-500">
            Aún no hay movimientos.
          </div>
        )}
        {!loading && entries.length > 0 && (
          <table className="w-full text-xs">
            <thead className="border-b border-[var(--color-border)] bg-neutral-50 text-neutral-500">
              <tr>
                <th className="px-3 py-2 text-left font-medium">Cuándo</th>
                <th className="px-3 py-2 text-left font-medium">Tipo</th>
                <th className="px-3 py-2 text-right font-medium">Cambio</th>
                <th className="px-3 py-2 text-right font-medium">Saldo</th>
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
      No pudimos cargar tu billing: {message}
    </div>
  );
}
