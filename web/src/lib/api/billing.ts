// API client for /api/billing/*.
//
// Same conventions as lib/api/scheduled-tasks.ts: plain fetch + Bearer
// Clerk JWT + workspace header. Errors carry the backend's `detail`
// string so the UI can surface "Stripe not configured" / "No active
// customer" etc. without parsing the response.

export type BillingCycle = "monthly" | "annual";

export type PlanOption = {
  name: string;
  display_name: string;
  monthly_price_floor: number;
  monthly_price_ceiling: number;
  credits_floor: number;
  credits_ceiling: number;
  description: string;
  annual_price_floor: number; // 12 * floor * 0.8
  has_monthly_checkout: boolean;
  has_annual_checkout: boolean;
};

export type BillingOverview = {
  plan: string;
  plan_display_name: string;
  billing_cycle: BillingCycle | null;
  credits_per_month: number;
  balance_credits: number;
  price_usd_monthly: number;
  current_period_start: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  is_unlimited: boolean;
  has_active_subscription: boolean;
  available_plans: PlanOption[];
  stripe_configured: boolean;
};

export type LedgerEntry = {
  id: string;
  delta_credits: number;
  kind: string;
  balance_after_credits: number;
  note: string | null;
  created_at: string;
};

function backendBase(): string {
  const url = process.env.NEXT_PUBLIC_BACKEND_URL;
  if (!url) {
    throw new Error(
      "NEXT_PUBLIC_BACKEND_URL is not set. Configure it in Doppler/.env.local.",
    );
  }
  return url.replace(/\/+$/, "");
}

function authHeaders(
  token: string,
  workspaceId?: string | null,
): Record<string, string> {
  const h: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };
  if (workspaceId) {
    h["X-Misterr-Workspace-Id"] = workspaceId;
  }
  return h;
}

async function expectOk(res: Response): Promise<Response> {
  if (res.ok) return res;
  let detail: unknown = null;
  try {
    detail = (await res.json())?.detail ?? null;
  } catch {
    // ignore; non-JSON error body
  }
  const message =
    typeof detail === "string"
      ? detail
      : detail
        ? JSON.stringify(detail)
        : `HTTP ${res.status}`;
  const err = new Error(message);
  (err as Error & { status?: number }).status = res.status;
  throw err;
}

export const billingApi = {
  overview: async (
    token: string,
    workspaceId?: string | null,
  ): Promise<BillingOverview> => {
    const res = await fetch(`${backendBase()}/api/billing/overview`, {
      headers: authHeaders(token, workspaceId),
    });
    await expectOk(res);
    return res.json();
  },

  ledger: async (
    token: string,
    workspaceId?: string | null,
    limit = 30,
  ): Promise<LedgerEntry[]> => {
    const url = new URL(`${backendBase()}/api/billing/ledger`);
    url.searchParams.set("limit", String(limit));
    const res = await fetch(url.toString(), {
      headers: authHeaders(token, workspaceId),
    });
    await expectOk(res);
    return res.json();
  },

  checkout: async (
    plan: string,
    cycle: BillingCycle,
    token: string,
    workspaceId?: string | null,
  ): Promise<{ url: string }> => {
    const res = await fetch(`${backendBase()}/api/billing/checkout`, {
      method: "POST",
      headers: authHeaders(token, workspaceId),
      body: JSON.stringify({ plan, cycle }),
    });
    await expectOk(res);
    return res.json();
  },

  portal: async (
    token: string,
    workspaceId?: string | null,
  ): Promise<{ url: string }> => {
    const res = await fetch(`${backendBase()}/api/billing/portal`, {
      method: "POST",
      headers: authHeaders(token, workspaceId),
    });
    await expectOk(res);
    return res.json();
  },
};
