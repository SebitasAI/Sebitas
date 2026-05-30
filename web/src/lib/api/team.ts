// API client for /api/team endpoints (slice T-5).
//
// Same conventions as scheduled-tasks: Bearer token from Clerk (template
// "backend") + NEXT_PUBLIC_BACKEND_URL. Errors carry `.status` so callers
// can branch on 403/404/409/422.

export type TeamMember = {
  clerk_user_id: string;
  role: "org:admin" | "org:member";
  email: string | null;
  name: string | null;
  image_url: string | null;
  app_user_id: string | null;
  slack_user_id: string | null;
  joined_at: string | null;
};

export type TeamMembersResponse = {
  members: TeamMember[];
  total: number;
};

export type InviteRequest = {
  email: string;
  role?: "org:admin" | "org:member";
  redirect_url?: string;
};

export type InviteResponse = {
  invitation_id: string;
  email: string;
  role: string;
};

export type SyncSlackResponse = {
  mode: "preview" | "apply";
  to_remove: {
    clerk_user_id: string;
    email: string | null;
    reason: string;
  }[];
  removed: string[];
};

export type ProvisionResponse = {
  orgs_created: number;
  members_linked: number;
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
    // non-JSON; ignore
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

export const teamApi = {
  listMembers: async (token: string): Promise<TeamMembersResponse> => {
    const res = await fetch(`${backendBase()}/api/team/members`, {
      headers: authHeaders(token),
    });
    await expectOk(res);
    return res.json();
  },
  invite: async (
    body: InviteRequest,
    token: string,
  ): Promise<InviteResponse> => {
    const res = await fetch(`${backendBase()}/api/team/invite`, {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify(body),
    });
    await expectOk(res);
    return res.json();
  },
  remove: async (clerkUserId: string, token: string): Promise<void> => {
    const res = await fetch(
      `${backendBase()}/api/team/members/${encodeURIComponent(clerkUserId)}`,
      {
        method: "DELETE",
        headers: authHeaders(token),
      },
    );
    await expectOk(res);
  },
  syncSlack: async (
    mode: "preview" | "apply",
    token: string,
  ): Promise<SyncSlackResponse> => {
    const res = await fetch(`${backendBase()}/api/team/sync-slack`, {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify({ mode }),
    });
    await expectOk(res);
    return res.json();
  },
  provision: async (token: string): Promise<ProvisionResponse> => {
    const res = await fetch(`${backendBase()}/api/team/provision`, {
      method: "POST",
      headers: authHeaders(token),
    });
    await expectOk(res);
    return res.json();
  },
};
