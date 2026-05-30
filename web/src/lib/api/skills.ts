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

export type SkillDetail = Skill & {
  body: string;
};

export type SkillCreateInput = {
  name: string;
  description: string;
  activation_default: "always_active" | "on_demand";
  scope: "workspace" | "personal";
  body: string;
  links: string[];
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
  detail: async (
    name: string,
    token: string,
    workspaceId?: string | null,
  ): Promise<SkillDetail> => {
    const res = await fetch(
      `${backendBase()}/api/skills/${encodeURIComponent(name)}`,
      {
        headers: authHeaders(token, workspaceId),
      },
    );
    await expectOk(res);
    return res.json();
  },
  create: async (
    payload: SkillCreateInput,
    token: string,
    workspaceId?: string | null,
  ): Promise<SkillDetail> => {
    const res = await fetch(`${backendBase()}/api/skills`, {
      method: "POST",
      headers: authHeaders(token, workspaceId),
      body: JSON.stringify(payload),
    });
    await expectOk(res);
    return res.json();
  },
};

// --------------------------------------------------------------------------- //
// Frontmatter parser (client-side; no deps)
// --------------------------------------------------------------------------- //
//
// Mini YAML frontmatter parser for `.md` files. Recognizes the
// `--- ... ---` header and pulls key:value pairs from inside. We keep it
// intentionally minimal -- this is for prefilling the upload form, not for
// strict YAML conformance.

export type Frontmatter = {
  name?: string;
  description?: string;
  activation?: "always_active" | "on_demand";
  scope?: "workspace" | "personal";
  links?: string[];
};

export type ParsedSkillMd = {
  frontmatter: Frontmatter;
  body: string;
};

export function parseSkillMarkdown(text: string): ParsedSkillMd {
  const match = text.match(/^---\s*\r?\n([\s\S]*?)\r?\n---\s*\r?\n?([\s\S]*)$/);
  if (!match) {
    return { frontmatter: {}, body: text };
  }
  const yaml = match[1];
  const body = match[2] ?? "";
  const fm: Frontmatter = {};
  for (const rawLine of yaml.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const m = line.match(/^([\w-]+)\s*:\s*(.*)$/);
    if (!m) continue;
    const key = m[1].toLowerCase();
    let value = m[2].trim().replace(/^['"]|['"]$/g, "");
    if (key === "name" || key === "description") {
      (fm as Record<string, unknown>)[key] = value;
    } else if (key === "activation" || key === "activation_default") {
      if (value === "always_active" || value === "on_demand") {
        fm.activation = value;
      }
    } else if (key === "scope") {
      if (value === "workspace" || value === "personal") {
        fm.scope = value;
      }
    } else if (key === "links") {
      // Either inline `[a, b]` or comma-separated. No multiline YAML lists.
      value = value.replace(/^\[|\]$/g, "");
      fm.links = value
        .split(",")
        .map((s) => s.trim().replace(/^['"]|['"]$/g, ""))
        .filter(Boolean);
    }
  }
  return { frontmatter: fm, body };
}
