// API client for /api/admin/* endpoints. Same auth/transport conventions as
// the other clients (Bearer Clerk JWT via template:"backend",
// NEXT_PUBLIC_BACKEND_URL, optional X-Misterr-Workspace-Id which the admin
// endpoints ignore).

export type AdminMeResponse = {
  is_admin: boolean;
  email: string | null;
};

export type WorkspaceSummary = {
  id: string;
  slack_team_id: string;
  name: string | null;
  installed_at: string | null;
  bot_user_id: string | null;
  bot_home_channel_id: string | null;
  user_count: number;
  skill_count: number;
  scheduled_task_count: number;
  integration_count: number;
};

export type WorkspacesResponse = {
  workspaces: WorkspaceSummary[];
  total_count: number;
};

export type UserSummary = {
  app_user_id: string;
  slack_user_id: string;
  display_name: string | null;
  real_name: string | null;
  email: string | null;
  tz: string | null;
  is_bot: boolean;
  deleted: boolean;
};

export type WorkspaceUsersResponse = {
  workspace_id: string;
  workspace_name: string | null;
  users: UserSummary[];
  total_count: number;
};

export type AdminScheduledTaskRow = {
  id: string;
  workspace_id: string;
  workspace_name: string | null;
  name: string;
  scope: string;
  cron_spec: string;
  timezone: string;
  is_paused: boolean;
  fire_once: boolean;
  prompt_is_literal: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_run_status: string | null;
  created_at: string;
};

export type AdminScheduledTasksResponse = {
  tasks: AdminScheduledTaskRow[];
  total_count: number;
};

export type AdminSkillRow = {
  id: string;
  workspace_id: string;
  workspace_name: string | null;
  name: string;
  description: string;
  scope: string;
  activation_default: string;
  source: string;
  version: number;
  size_bytes: number;
  created_by_user_id: string | null;
  created_at: string;
};

export type AdminSkillsResponse = {
  skills: AdminSkillRow[];
  total_count: number;
};

export type AdminIntegrationRow = {
  id: string;
  workspace_id: string;
  workspace_name: string | null;
  app: string;
  provider: string;
  status: string;
  created_at: string;
};

export type AdminIntegrationsResponse = {
  integrations: AdminIntegrationRow[];
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
    // non-JSON
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

export const adminApi = {
  me: async (token: string): Promise<AdminMeResponse> => {
    const res = await fetch(`${backendBase()}/api/admin/me`, {
      headers: authHeaders(token),
    });
    await expectOk(res);
    return res.json();
  },
  workspaces: async (token: string): Promise<WorkspacesResponse> => {
    const res = await fetch(`${backendBase()}/api/admin/workspaces`, {
      headers: authHeaders(token),
    });
    await expectOk(res);
    return res.json();
  },
  workspaceUsers: async (
    workspaceId: string,
    token: string,
  ): Promise<WorkspaceUsersResponse> => {
    const res = await fetch(
      `${backendBase()}/api/admin/workspaces/${encodeURIComponent(workspaceId)}/users`,
      { headers: authHeaders(token) },
    );
    await expectOk(res);
    return res.json();
  },
  scheduledTasks: async (
    token: string,
    workspaceId?: string,
  ): Promise<AdminScheduledTasksResponse> => {
    const url = new URL(`${backendBase()}/api/admin/scheduled-tasks`);
    if (workspaceId) url.searchParams.set("workspace_id", workspaceId);
    const res = await fetch(url.toString(), { headers: authHeaders(token) });
    await expectOk(res);
    return res.json();
  },
  skills: async (
    token: string,
    workspaceId?: string,
  ): Promise<AdminSkillsResponse> => {
    const url = new URL(`${backendBase()}/api/admin/skills`);
    if (workspaceId) url.searchParams.set("workspace_id", workspaceId);
    const res = await fetch(url.toString(), { headers: authHeaders(token) });
    await expectOk(res);
    return res.json();
  },
  integrations: async (
    token: string,
    workspaceId?: string,
  ): Promise<AdminIntegrationsResponse> => {
    const url = new URL(`${backendBase()}/api/admin/integrations`);
    if (workspaceId) url.searchParams.set("workspace_id", workspaceId);
    const res = await fetch(url.toString(), { headers: authHeaders(token) });
    await expectOk(res);
    return res.json();
  },
};
