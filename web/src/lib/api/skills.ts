// API client for /api/skills endpoints. Same auth + transport conventions
// as `lib/api/scheduled-tasks.ts`: NEXT_PUBLIC_BACKEND_URL, Bearer Clerk
// JWT (template: "backend"), optional X-Misterr-Workspace-Id header for
// multi-workspace users.

export type Skill = {
  id: string;
  name: string;
  description: string;
  scope: "workspace" | "personal";
  activation_default: "always_active" | "on_demand";
  activation_override: "always_active" | "on_demand" | null;
  effective_activation: "always_active" | "on_demand";
  source: string;
  version: number;
  links: string[];
  size_bytes: number;
  created_at: string;
  created_by_user_id: string | null;
  is_installed: boolean;
  is_mine: boolean;
};

export type SkillListResponse = {
  skills: Skill[];
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
    // non-JSON body
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

export const skillsApi = {
  list: async (
    token: string,
    workspaceId?: string | null,
  ): Promise<SkillListResponse> => {
    const res = await fetch(`${backendBase()}/api/skills`, {
      headers: authHeaders(token, workspaceId),
    });
    await expectOk(res);
    return res.json();
  },
  install: async (
    name: string,
    token: string,
    workspaceId?: string | null,
  ): Promise<Skill> => {
    const res = await fetch(
      `${backendBase()}/api/skills/${encodeURIComponent(name)}/install`,
      {
        method: "POST",
        headers: authHeaders(token, workspaceId),
      },
    );
    await expectOk(res);
    return res.json();
  },
  uninstall: async (
    name: string,
    token: string,
    workspaceId?: string | null,
  ): Promise<Skill> => {
    const res = await fetch(
      `${backendBase()}/api/skills/${encodeURIComponent(name)}/uninstall`,
      {
        method: "POST",
        headers: authHeaders(token, workspaceId),
      },
    );
    await expectOk(res);
    return res.json();
  },
};
