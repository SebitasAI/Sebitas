// API client for /api/integrations endpoints (slice T-6).
//
// Same auth conventions as the rest of the web app: Bearer Clerk JWT
// (template "backend") + NEXT_PUBLIC_BACKEND_URL. Errors carry `.status`
// so callers can branch on 403/404/409/etc.

export type CatalogApp = {
  slug: string;
  name: string;
  description: string | null;
  logo_url: string | null;
  provider: "composio" | "pipedream";
  categories: string[];
  popular: boolean;
};

export type CatalogResponse = {
  apps: CatalogApp[];
  total: number;
};

export type Connection = {
  id: string;
  app: string;
  provider: "composio" | "pipedream";
  status: string;
  scope: "team" | "private";
  account_label: string | null;
  owner_user_id: string | null;
  owner_display: string | null;
  created_at: string | null;
};

export type ConnectionsResponse = {
  app: string | null;
  connections: Connection[];
  total: number;
};

export type CreateConnectionRequest = {
  app: string;
  scope?: "team" | "private";
  account_label?: string | null;
  redirect_url?: string | null;
};

export type CreateConnectionResponse = {
  connection_id: string;
  connect_url: string;
  provider: "composio" | "pipedream";
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

export const integrationsApi = {
  listCatalog: async (
    onlyPopular: boolean,
    token: string,
  ): Promise<CatalogResponse> => {
    const url = new URL(`${backendBase()}/api/integrations/catalog`);
    if (onlyPopular) url.searchParams.set("only_popular", "true");
    const res = await fetch(url.toString(), { headers: authHeaders(token) });
    await expectOk(res);
    return res.json();
  },
  listConnections: async (
    app: string | null,
    token: string,
  ): Promise<ConnectionsResponse> => {
    const url = new URL(`${backendBase()}/api/integrations/connections`);
    if (app) url.searchParams.set("app", app);
    const res = await fetch(url.toString(), { headers: authHeaders(token) });
    await expectOk(res);
    return res.json();
  },
  createConnection: async (
    body: CreateConnectionRequest,
    token: string,
  ): Promise<CreateConnectionResponse> => {
    const res = await fetch(`${backendBase()}/api/integrations/connections`, {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify(body),
    });
    await expectOk(res);
    return res.json();
  },
  deleteConnection: async (id: string, token: string): Promise<void> => {
    const res = await fetch(
      `${backendBase()}/api/integrations/connections/${encodeURIComponent(id)}`,
      {
        method: "DELETE",
        headers: authHeaders(token),
      },
    );
    await expectOk(res);
  },
};
