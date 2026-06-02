// API client for /api/usage/*.
//
// Mirrors lib/api/scheduled-tasks.ts: plain fetch + Bearer Clerk JWT.
// Read-only -- the Usage page never mutates anything.

export type DateRange = "7d" | "30d" | "90d" | "all";

export type ActivityKindFilter =
  | "all"
  | "slack_thread"
  | "scheduled_task"
  | "automation"
  | "media";

export type OverviewDailyBucket = {
  date: string; // YYYY-MM-DD
  threads: number;
  scheduled_tasks: number;
  automations: number;
  media: number;
};

export type OverviewTopUser = {
  app_user_id: string | null;
  display_name: string;
  credits: number;
};

export type OverviewTopScheduledTask = {
  task_id: string | null;
  name: string;
  credits: number;
};

export type OverviewResponse = {
  total_credits: number;
  burn_per_day: number;
  days_in_range: number;
  daily: OverviewDailyBucket[];
  category_pct: Record<string, number>;
  top_users: OverviewTopUser[];
  top_scheduled_tasks: OverviewTopScheduledTask[];
};

export type TeamRow = {
  app_user_id: string | null;
  display_name: string;
  threads: number;
  scheduled_task_runs: number;
  automation_runs: number;
  credits: number;
  last_activity: string | null;
};

export type TeamResponse = {
  total_users: number;
  rows: TeamRow[];
};

export type ActivityRow = {
  id: string;
  kind: string;
  parent_name: string | null;
  user_display_name: string;
  user_id: string | null;
  credits: number;
  started_at: string;
  slack_channel_id: string | null;
  slack_thread_ts: string | null;
  status: string;
};

export type ActivityResponse = {
  rows: ActivityRow[];
  page: number;
  page_size: number;
  total_count: number;
};

export type ScheduledTaskRow = {
  task_id: string | null;
  name: string;
  total_runs: number;
  last_activity: string | null;
  created_by_user_id: string | null;
  created_by_display_name: string;
  total_credits: number;
  is_system: boolean;
};

export type ScheduledTaskResponse = {
  rows: ScheduledTaskRow[];
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

function authHeaders(token: string): Record<string, string> {
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };
}

async function expectOk(res: Response): Promise<Response> {
  if (res.ok) return res;
  let detail: unknown = null;
  try {
    detail = (await res.json())?.detail ?? null;
  } catch {
    // non-JSON error body
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

export const usageApi = {
  overview: async (
    range: DateRange,
    token: string,
  ): Promise<OverviewResponse> => {
    const url = new URL(`${backendBase()}/api/usage/overview`);
    url.searchParams.set("range", range);
    const res = await fetch(url.toString(), { headers: authHeaders(token) });
    await expectOk(res);
    return res.json();
  },
  team: async (range: DateRange, token: string): Promise<TeamResponse> => {
    const url = new URL(`${backendBase()}/api/usage/team`);
    url.searchParams.set("range", range);
    const res = await fetch(url.toString(), { headers: authHeaders(token) });
    await expectOk(res);
    return res.json();
  },
  activity: async (
    args: {
      range: DateRange;
      userId?: string | null;
      taskId?: string | null;
      kind?: ActivityKindFilter;
      page?: number;
      pageSize?: number;
    },
    token: string,
  ): Promise<ActivityResponse> => {
    const url = new URL(`${backendBase()}/api/usage/activity`);
    url.searchParams.set("range", args.range);
    if (args.userId) url.searchParams.set("user_id", args.userId);
    if (args.taskId) url.searchParams.set("task_id", args.taskId);
    if (args.kind && args.kind !== "all")
      url.searchParams.set("kind", args.kind);
    if (args.page) url.searchParams.set("page", String(args.page));
    if (args.pageSize)
      url.searchParams.set("page_size", String(args.pageSize));
    const res = await fetch(url.toString(), { headers: authHeaders(token) });
    await expectOk(res);
    return res.json();
  },
  scheduledTasks: async (
    args: {
      range: DateRange;
      systemOnly?: boolean;
      userId?: string | null;
    },
    token: string,
  ): Promise<ScheduledTaskResponse> => {
    const url = new URL(`${backendBase()}/api/usage/scheduled-tasks`);
    url.searchParams.set("range", args.range);
    if (args.systemOnly) url.searchParams.set("system_only", "true");
    if (args.userId) url.searchParams.set("user_id", args.userId);
    const res = await fetch(url.toString(), { headers: authHeaders(token) });
    await expectOk(res);
    return res.json();
  },
};
