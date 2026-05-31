// API client for the Misterr /api/scheduled-tasks endpoints.
//
// The browser calls the backend directly (CORS-enabled). Auth is a Bearer
// Clerk JWT obtained from `useAuth().getToken()` in the calling component
// and passed in as `token`. We don't ship an Axios instance or interceptors
// -- plain fetch + per-request token is enough for v1.
//
// `NEXT_PUBLIC_BACKEND_URL` is required at build time (set in Doppler).
// Without it, all calls fail with a clear runtime error rather than silently
// hitting the wrong host.

export type ScheduledTask = {
  id: string;
  name: string;
  prompt: string;
  cron_spec: string;
  cron_human: string | null;
  timezone: string;
  scope: "local" | "global" | "system";
  destination_type: "channel" | "dm";
  destination_slack_id: string | null;
  is_paused: boolean;
  paused_until: string | null;
  last_run_at: string | null;
  last_run_status: "success" | "failed" | "running" | null;
  last_run_error: string | null;
  last_run_summary: string | null;
  next_run_at: string | null;
  created_at: string;
  created_by_user_id: string | null;
  fire_once: boolean;
  prompt_is_literal: boolean;
};

export type TaskListResponse = {
  tasks: ScheduledTask[];
  total_count: number;
};

export type ListFilter = "all" | "mine" | "system";

export type ScheduledTaskRun = {
  id: string;
  task_id: string | null;
  task_name_snapshot: string;
  started_at: string;
  finished_at: string | null;
  status: "success" | "failed" | "running";
  output: string | null;
  error: string | null;
};

export type TaskRunsResponse = {
  runs: ScheduledTaskRun[];
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
  // Surface status code so the caller can branch on 403 / 404 etc.
  (err as Error & { status?: number }).status = res.status;
  throw err;
}

export const scheduledTasksApi = {
  list: async (
    filter: ListFilter,
    token: string,
    workspaceId?: string | null,
  ): Promise<TaskListResponse> => {
    const url = new URL(`${backendBase()}/api/scheduled-tasks`);
    url.searchParams.set("filter", filter);
    const res = await fetch(url.toString(), {
      headers: authHeaders(token, workspaceId),
    });
    await expectOk(res);
    return res.json();
  },
  pause: async (
    idOrName: string,
    until: string | null,
    token: string,
    workspaceId?: string | null,
  ): Promise<ScheduledTask> => {
    const res = await fetch(
      `${backendBase()}/api/scheduled-tasks/${encodeURIComponent(idOrName)}/pause`,
      {
        method: "POST",
        headers: authHeaders(token, workspaceId),
        body: JSON.stringify({ until }),
      },
    );
    await expectOk(res);
    return res.json();
  },
  resume: async (
    idOrName: string,
    token: string,
    workspaceId?: string | null,
  ): Promise<ScheduledTask> => {
    const res = await fetch(
      `${backendBase()}/api/scheduled-tasks/${encodeURIComponent(idOrName)}/resume`,
      {
        method: "POST",
        headers: authHeaders(token, workspaceId),
      },
    );
    await expectOk(res);
    return res.json();
  },
  runs: async (
    idOrName: string,
    token: string,
    workspaceId?: string | null,
    limit = 50,
  ): Promise<TaskRunsResponse> => {
    const url = new URL(
      `${backendBase()}/api/scheduled-tasks/${encodeURIComponent(idOrName)}/runs`,
    );
    url.searchParams.set("limit", String(limit));
    const res = await fetch(url.toString(), {
      headers: authHeaders(token, workspaceId),
    });
    await expectOk(res);
    return res.json();
  },
};
