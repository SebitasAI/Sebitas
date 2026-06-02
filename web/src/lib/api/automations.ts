// API client for the Misterr /api/automations endpoints.
//
// Same shape as scheduled-tasks.ts: plain fetch + Bearer Clerk JWT.
// The web reads + pauses/resumes + rotates the direct-URL secret;
// create/update/delete remain chat-only (the agent has the preview +
// confirmation UX).

export type AutomationSource = "direct" | "pipedream" | "composio";

export type AutomationScope = "local" | "global" | "system";

export type Automation = {
  id: string;
  name: string;
  description: string | null;
  source: AutomationSource;
  prompt_template: string;
  destination_channel: string | null;
  // Only populated when source === "direct". URL the user pastes
  // into their external system. Null for pipedream/composio (the
  // URL is between Misterr and the provider).
  webhook_url: string | null;
  external_trigger_id: string | null;
  trigger_metadata: Record<string, unknown>;
  scope: AutomationScope;
  is_paused: boolean;
  last_fired_at: string | null;
  last_fire_status: "success" | "failed" | "skipped" | null;
  last_fire_error: string | null;
  fire_count: number;
  created_at: string;
  created_by_user_id: string | null;
  owner_user_id: string | null;
};

export type AutomationListResponse = {
  automations: Automation[];
  total_count: number;
};

export type AutomationListFilter = "mine" | "all";

export type AutomationRun = {
  id: string;
  automation_id: string | null;
  automation_name_snapshot: string;
  trigger_payload: Record<string, unknown>;
  prompt_template_snapshot: string;
  rendered_prompt: string | null;
  started_at: string;
  finished_at: string | null;
  status: "running" | "success" | "failed" | "skipped";
  output: string | null;
  error: string | null;
};

export type AutomationRunsResponse = {
  runs: AutomationRun[];
  total_count: number;
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

export const automationsApi = {
  list: async (
    filter: AutomationListFilter,
    token: string,
    workspaceId?: string | null,
  ): Promise<AutomationListResponse> => {
    const url = new URL(`${backendBase()}/api/automations`);
    url.searchParams.set("filter", filter);
    const res = await fetch(url.toString(), {
      headers: authHeaders(token, workspaceId),
    });
    await expectOk(res);
    return res.json();
  },
  pause: async (
    handle: string,
    token: string,
    workspaceId?: string | null,
  ): Promise<Automation> => {
    const res = await fetch(
      `${backendBase()}/api/automations/${encodeURIComponent(handle)}/pause`,
      {
        method: "POST",
        headers: authHeaders(token, workspaceId),
      },
    );
    await expectOk(res);
    return res.json();
  },
  resume: async (
    handle: string,
    token: string,
    workspaceId?: string | null,
  ): Promise<Automation> => {
    const res = await fetch(
      `${backendBase()}/api/automations/${encodeURIComponent(handle)}/resume`,
      {
        method: "POST",
        headers: authHeaders(token, workspaceId),
      },
    );
    await expectOk(res);
    return res.json();
  },
  rotateUrl: async (
    handle: string,
    token: string,
    workspaceId?: string | null,
  ): Promise<Automation> => {
    const res = await fetch(
      `${backendBase()}/api/automations/${encodeURIComponent(handle)}/rotate-url`,
      {
        method: "POST",
        headers: authHeaders(token, workspaceId),
      },
    );
    await expectOk(res);
    return res.json();
  },
  runs: async (
    handle: string,
    token: string,
    workspaceId?: string | null,
    limit = 50,
  ): Promise<AutomationRunsResponse> => {
    const url = new URL(
      `${backendBase()}/api/automations/${encodeURIComponent(handle)}/runs`,
    );
    url.searchParams.set("limit", String(limit));
    const res = await fetch(url.toString(), {
      headers: authHeaders(token, workspaceId),
    });
    await expectOk(res);
    return res.json();
  },
};
