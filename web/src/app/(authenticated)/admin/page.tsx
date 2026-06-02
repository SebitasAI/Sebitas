"use client";

// Read-only /admin page (slice T-8). Cross-workspace overview for platform
// admins. Auth is enforced server-side via PLATFORM_ADMINS env var; the page
// itself just renders whatever the backend returns. A non-admin hitting /api/
// admin/* gets a 403 and we show a friendly "not authorized" block.

import { useMemo, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck, ChevronDown, ChevronRight, X } from "lucide-react";
import { formatDistanceToNow } from "date-fns";

import { PageBody, PageHeader } from "../_components/page-header";
import {
  adminApi,
  type WorkspaceSummary,
  type WorkspacesResponse,
  type WorkspaceUsersResponse,
  type AdminScheduledTasksResponse,
  type AdminSkillRow,
  type AdminSkillsResponse,
  type AdminIntegrationsResponse,
} from "@/lib/api/admin";

export default function AdminPage() {
  return (
    <>
      <PageHeader title="Admin" Icon={ShieldCheck} />
      <PageBody>
        <AdminBody />
      </PageBody>
    </>
  );
}

function AdminBody() {
  const { getToken } = useAuth();

  // First check: am I admin? If not, render a friendly block instead of
  // firing every subsequent admin query.
  const meQuery = useQuery({
    queryKey: ["admin", "me"],
    queryFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return adminApi.me(token);
    },
  });

  if (meQuery.isLoading) {
    return <div className="text-sm text-neutral-500">Verificando permisos…</div>;
  }
  if (meQuery.isError) {
    return (
      <ErrorBlock
        message={(meQuery.error as Error)?.message ?? "Error desconocido"}
      />
    );
  }
  if (!meQuery.data?.is_admin) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
        <div className="font-medium">No tenés permisos de platform admin.</div>
        <p className="mt-1 text-xs text-amber-700">
          Tu email{" "}
          <code className="rounded bg-white/60 px-1">
            {meQuery.data?.email ?? "(desconocido)"}
          </code>{" "}
          no está en la lista <code>PLATFORM_ADMINS</code>. Si necesitás
          acceso, pediselo a quien gestione Doppler.
        </p>
      </div>
    );
  }

  return <AdminDashboard />;
}

function AdminDashboard() {
  const { getToken } = useAuth();

  const wsQuery = useQuery({
    queryKey: ["admin", "workspaces"],
    queryFn: async (): Promise<WorkspacesResponse> => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return adminApi.workspaces(token);
    },
  });

  const [tab, setTab] = useState<"workspaces" | "scheduled-tasks" | "skills" | "integrations">(
    "workspaces",
  );
  const [filterWs, setFilterWs] = useState<string | null>(null);

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm text-neutral-500">
        Read-only overview de toda la plataforma. Filtrá por workspace usando
        el dropdown a la derecha de cada tab.
      </p>

      <Tabs value={tab} onChange={setTab} />

      {tab === "workspaces" ? (
        <WorkspacesTab
          query={wsQuery}
          onPickWorkspace={(id) => {
            setFilterWs(id);
            setTab("scheduled-tasks");
          }}
        />
      ) : (
        <FilteredView
          tab={tab}
          workspaces={wsQuery.data?.workspaces ?? []}
          filterWs={filterWs}
          onChangeFilter={setFilterWs}
        />
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Tabs
// --------------------------------------------------------------------------- //

function Tabs({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: "workspaces" | "scheduled-tasks" | "skills" | "integrations") => void;
}) {
  const items: { id: typeof value; label: string }[] = [
    { id: "workspaces", label: "Workspaces" },
    { id: "scheduled-tasks", label: "Scheduled tasks" },
    { id: "skills", label: "Skills" },
    { id: "integrations", label: "Integrations" },
  ];
  return (
    <div role="tablist" className="flex gap-1 rounded-lg bg-[var(--color-surface-fog)] p-1 text-sm">
      {items.map((it) => {
        const active = it.id === value;
        return (
          <button
            key={it.id}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(it.id as never)}
            className={`rounded-md px-3 py-1.5 transition-colors ${
              active
                ? "bg-white text-[var(--color-ink-deep)] shadow-sm"
                : "text-neutral-600 hover:text-[var(--color-ink-deep)]"
            }`}
          >
            {it.label}
          </button>
        );
      })}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Workspaces tab (with expand-to-show-users)
// --------------------------------------------------------------------------- //

function WorkspacesTab({
  query,
  onPickWorkspace,
}: {
  query: ReturnType<typeof useQuery<WorkspacesResponse>>;
  onPickWorkspace: (id: string) => void;
}) {
  if (query.isLoading) {
    return <div className="text-sm text-neutral-500">Cargando workspaces…</div>;
  }
  if (query.isError) {
    return (
      <ErrorBlock
        message={(query.error as Error)?.message ?? "Error desconocido"}
      />
    );
  }
  const workspaces = query.data?.workspaces ?? [];
  if (workspaces.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-[var(--color-border)] bg-white py-10 text-center text-sm text-neutral-500">
        No hay workspaces todavía.
      </div>
    );
  }
  return (
    <ul className="flex flex-col gap-2">
      {workspaces.map((w) => (
        <WorkspaceRow
          key={w.id}
          workspace={w}
          onPickWorkspace={onPickWorkspace}
        />
      ))}
    </ul>
  );
}

function WorkspaceRow({
  workspace,
  onPickWorkspace,
}: {
  workspace: WorkspaceSummary;
  onPickWorkspace: (id: string) => void;
}) {
  const { getToken } = useAuth();
  const [expanded, setExpanded] = useState(false);

  const usersQuery = useQuery({
    queryKey: ["admin", "workspace-users", workspace.id],
    enabled: expanded,
    queryFn: async (): Promise<WorkspaceUsersResponse> => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return adminApi.workspaceUsers(workspace.id, token);
    },
  });

  return (
    <li className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-white">
      <header className="flex items-center justify-between gap-3 px-4 py-3">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex flex-1 items-center gap-2 text-left"
        >
          <span className="text-neutral-400">
            {expanded ? (
              <ChevronDown className="size-4" strokeWidth={1.75} />
            ) : (
              <ChevronRight className="size-4" strokeWidth={1.75} />
            )}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium text-[var(--color-ink-deep)]">
                {workspace.name ?? workspace.slack_team_id}
              </span>
              <span className="text-[11px] text-neutral-400">
                {workspace.slack_team_id}
              </span>
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-3 text-[11px] text-neutral-500">
              <span>👥 {workspace.user_count} users</span>
              <span>🛠 {workspace.skill_count} skills</span>
              <span>⏰ {workspace.scheduled_task_count} tasks</span>
              <span>🔌 {workspace.integration_count} integrations</span>
              {workspace.installed_at ? (
                <span>
                  instalado{" "}
                  {formatDistanceToNow(new Date(workspace.installed_at), {
                    addSuffix: true,
                  })}
                </span>
              ) : (
                <span className="text-amber-600">no instalado</span>
              )}
            </div>
          </div>
        </button>
        <button
          type="button"
          onClick={() => onPickWorkspace(workspace.id)}
          className="rounded-md border border-[var(--color-border)] bg-white px-2.5 py-1 text-[11px] font-medium text-neutral-600 hover:text-[var(--color-ink-deep)]"
        >
          Filtrar
        </button>
      </header>
      {expanded ? (
        <div className="border-t border-[var(--color-border)] bg-[var(--color-surface-fog)]/40 px-4 py-3 text-xs">
          {usersQuery.isLoading ? (
            <div className="text-neutral-500">Cargando users…</div>
          ) : usersQuery.isError ? (
            <ErrorBlock
              message={
                (usersQuery.error as Error)?.message ?? "Error desconocido"
              }
            />
          ) : usersQuery.data?.users.length === 0 ? (
            <div className="text-neutral-500">Sin users registrados.</div>
          ) : (
            <table className="w-full text-left">
              <thead className="text-[10px] uppercase tracking-wide text-neutral-500">
                <tr>
                  <th className="py-1">Display</th>
                  <th>Email</th>
                  <th>TZ</th>
                  <th>Slack id</th>
                  <th>Estado</th>
                </tr>
              </thead>
              <tbody>
                {usersQuery.data?.users.map((u) => (
                  <tr key={u.app_user_id} className="border-t border-[var(--color-border)]">
                    <td className="py-1">{u.display_name ?? u.real_name ?? "—"}</td>
                    <td className="font-mono text-[11px]">{u.email ?? "—"}</td>
                    <td>{u.tz ?? "—"}</td>
                    <td className="font-mono text-[11px]">{u.slack_user_id}</td>
                    <td>
                      {u.deleted ? (
                        <span className="text-red-600">deleted</span>
                      ) : u.is_bot ? (
                        <span className="text-neutral-500">bot</span>
                      ) : (
                        <span className="text-emerald-600">active</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : null}
    </li>
  );
}

// --------------------------------------------------------------------------- //
// Filtered views (scheduled tasks, skills, integrations)
// --------------------------------------------------------------------------- //

function FilteredView({
  tab,
  workspaces,
  filterWs,
  onChangeFilter,
}: {
  tab: "scheduled-tasks" | "skills" | "integrations";
  workspaces: WorkspaceSummary[];
  filterWs: string | null;
  onChangeFilter: (v: string | null) => void;
}) {
  const { getToken } = useAuth();

  const tasksQuery = useQuery({
    queryKey: ["admin", "scheduled-tasks", filterWs],
    enabled: tab === "scheduled-tasks",
    queryFn: async (): Promise<AdminScheduledTasksResponse> => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return adminApi.scheduledTasks(token, filterWs ?? undefined);
    },
  });
  const skillsQuery = useQuery({
    queryKey: ["admin", "skills", filterWs],
    enabled: tab === "skills",
    queryFn: async (): Promise<AdminSkillsResponse> => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return adminApi.skills(token, filterWs ?? undefined);
    },
  });
  const integrationsQuery = useQuery({
    queryKey: ["admin", "integrations", filterWs],
    enabled: tab === "integrations",
    queryFn: async (): Promise<AdminIntegrationsResponse> => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return adminApi.integrations(token, filterWs ?? undefined);
    },
  });

  return (
    <div className="flex flex-col gap-3">
      <WorkspaceFilter
        workspaces={workspaces}
        value={filterWs}
        onChange={onChangeFilter}
      />
      {tab === "scheduled-tasks" ? (
        <ScheduledTasksTable query={tasksQuery} />
      ) : tab === "skills" ? (
        <SkillsTable query={skillsQuery} />
      ) : (
        <IntegrationsTable query={integrationsQuery} />
      )}
    </div>
  );
}

function WorkspaceFilter({
  workspaces,
  value,
  onChange,
}: {
  workspaces: WorkspaceSummary[];
  value: string | null;
  onChange: (v: string | null) => void;
}) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <label className="text-[11px] uppercase tracking-wide text-neutral-500">
        Workspace
      </label>
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
        className="rounded-md border border-[var(--color-border)] bg-white px-2 py-1 text-sm text-[var(--color-ink-deep)] focus:border-[#FF5200] focus:outline-none focus:ring-1 focus:ring-[#FF5200]/30"
      >
        <option value="">Todos</option>
        {workspaces.map((w) => (
          <option key={w.id} value={w.id}>
            {w.name ?? w.slack_team_id}
          </option>
        ))}
      </select>
    </div>
  );
}

function ScheduledTasksTable({
  query,
}: {
  query: ReturnType<typeof useQuery<AdminScheduledTasksResponse>>;
}) {
  if (query.isLoading) return <Loader />;
  if (query.isError)
    return <ErrorBlock message={(query.error as Error).message} />;
  const tasks = query.data?.tasks ?? [];
  if (tasks.length === 0) return <EmptyBlock label="scheduled tasks" />;
  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--color-border)] bg-white">
      <table className="w-full text-left text-xs">
        <thead className="bg-[var(--color-surface-fog)] text-[10px] uppercase tracking-wide text-neutral-500">
          <tr>
            <th className="px-3 py-2">Workspace</th>
            <th className="px-3 py-2">Name</th>
            <th className="px-3 py-2">Scope</th>
            <th className="px-3 py-2">Cron</th>
            <th className="px-3 py-2">Next</th>
            <th className="px-3 py-2">Last</th>
            <th className="px-3 py-2">Estado</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((t) => (
            <tr key={t.id} className="border-t border-[var(--color-border)]">
              <td className="px-3 py-2">{t.workspace_name ?? "—"}</td>
              <td className="px-3 py-2 font-mono">{t.name}</td>
              <td className="px-3 py-2">
                <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[10px]">
                  {t.scope}
                </span>
                {t.fire_once ? (
                  <span className="ml-1 rounded-full bg-violet-100 px-2 py-0.5 text-[10px] text-violet-700">
                    once
                  </span>
                ) : null}
              </td>
              <td className="px-3 py-2 font-mono">{t.cron_spec}</td>
              <td className="px-3 py-2">
                {t.next_run_at
                  ? formatDistanceToNow(new Date(t.next_run_at), {
                      addSuffix: true,
                    })
                  : "—"}
              </td>
              <td className="px-3 py-2">
                {t.last_run_at
                  ? formatDistanceToNow(new Date(t.last_run_at), {
                      addSuffix: true,
                    })
                  : "—"}
              </td>
              <td className="px-3 py-2">
                {t.is_paused ? (
                  <span className="text-yellow-700">paused</span>
                ) : t.last_run_status === "failed" ? (
                  <span className="text-red-600">failed</span>
                ) : t.last_run_status === "success" ? (
                  <span className="text-emerald-600">ok</span>
                ) : (
                  <span className="text-neutral-500">pending</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SkillsTable({
  query,
}: {
  query: ReturnType<typeof useQuery<AdminSkillsResponse>>;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  if (query.isLoading) return <Loader />;
  if (query.isError)
    return <ErrorBlock message={(query.error as Error).message} />;
  const skills = query.data?.skills ?? [];
  if (skills.length === 0) return <EmptyBlock label="skills" />;
  return (
    <>
      <div className="overflow-x-auto rounded-lg border border-[var(--color-border)] bg-white">
        <table className="w-full text-left text-xs">
          <thead className="bg-[var(--color-surface-fog)] text-[10px] uppercase tracking-wide text-neutral-500">
            <tr>
              <th className="px-3 py-2">Workspace</th>
              <th className="px-3 py-2">Name</th>
              <th className="px-3 py-2">Source</th>
              <th className="px-3 py-2">Scope</th>
              <th className="px-3 py-2">Activación</th>
              <th className="px-3 py-2">Size</th>
              <th className="px-3 py-2">Descripción</th>
              <th className="px-3 py-2 text-right">Acciones</th>
            </tr>
          </thead>
          <tbody>
            {skills.map((s) => (
              <tr key={s.id} className="border-t border-[var(--color-border)]">
                <td className="px-3 py-2">{s.workspace_name ?? "—"}</td>
                <td className="px-3 py-2 font-mono">{s.name}</td>
                <td className="px-3 py-2">
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                      s.source === "memory"
                        ? "bg-purple-50 text-purple-700"
                        : s.source === "catalog"
                          ? "bg-blue-50 text-blue-700"
                          : "bg-neutral-100 text-neutral-700"
                    }`}
                  >
                    {s.source}
                  </span>
                </td>
                <td className="px-3 py-2">{s.scope}</td>
                <td className="px-3 py-2">{s.activation_default}</td>
                <td className="px-3 py-2">
                  {Math.max(1, Math.round(s.size_bytes / 1024))} KB
                </td>
                <td className="px-3 py-2">{s.description}</td>
                <td className="px-3 py-2 text-right">
                  <button
                    type="button"
                    onClick={() => setSelectedId(s.id)}
                    className="rounded border border-[var(--color-border)] bg-white px-2 py-1 text-[11px] font-medium hover:bg-neutral-50"
                  >
                    Ver / Editar
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selectedId && (
        <SkillDetailModal
          skillId={selectedId}
          onClose={() => setSelectedId(null)}
          rows={skills}
        />
      )}
    </>
  );
}

function SkillDetailModal({
  skillId,
  onClose,
  rows,
}: {
  skillId: string;
  onClose: () => void;
  rows: AdminSkillRow[];
}) {
  const { getToken } = useAuth();
  const qc = useQueryClient();
  const summary = rows.find((r) => r.id === skillId);
  const isMemory = summary?.source === "memory";

  const detailQuery = useQuery({
    queryKey: ["admin", "skill", skillId],
    queryFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk token");
      return adminApi.skillDetail(skillId, token);
    },
  });

  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const saveMutation = useMutation({
    mutationFn: async (body: string) => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk token");
      return adminApi.updateSkillBody(skillId, body, token);
    },
    onSuccess: () => {
      setError(null);
      setDraft(null);
      qc.invalidateQueries({ queryKey: ["admin", "skill", skillId] });
      qc.invalidateQueries({ queryKey: ["admin", "skills"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const deleteMutation = useMutation({
    mutationFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk token");
      return adminApi.deleteSkill(skillId, token);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "skills"] });
      onClose();
    },
    onError: (e: Error) => setError(e.message),
  });

  const body =
    draft !== null ? draft : (detailQuery.data?.body ?? "");
  const isDirty = draft !== null && draft !== detailQuery.data?.body;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-4xl flex-col rounded-lg bg-white shadow-xl">
        <div className="flex items-start justify-between border-b border-[var(--color-border)] px-5 py-3">
          <div>
            <h2 className="font-mono text-sm font-semibold">
              {summary?.name ?? skillId}
            </h2>
            <p className="mt-0.5 text-xs text-neutral-500">
              {summary?.workspace_name ?? "—"} · {summary?.source} · {summary?.scope} ·{" "}
              v{summary?.version}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-neutral-500 hover:bg-neutral-100"
            aria-label="Cerrar"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {isMemory && (
          <div className="mx-5 mt-3 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            <strong>Memoria.</strong> Mantené las secciones{" "}
            <code className="font-mono">## Curated summary</code> y{" "}
            <code className="font-mono">## Observations log</code>. Editar mal
            puede romper la compactación.
          </div>
        )}

        <div className="flex-1 overflow-auto p-5">
          {detailQuery.isLoading ? (
            <Loader />
          ) : detailQuery.isError ? (
            <ErrorBlock message={(detailQuery.error as Error).message} />
          ) : (
            <textarea
              value={body}
              onChange={(e) => setDraft(e.target.value)}
              spellCheck={false}
              className="h-[55vh] w-full resize-none rounded border border-[var(--color-border)] bg-[var(--color-surface-fog)] p-3 font-mono text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-blue-200"
            />
          )}
        </div>

        {error && (
          <div className="mx-5 mb-2 rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700">
            {error}
          </div>
        )}

        <div className="flex items-center justify-between gap-3 border-t border-[var(--color-border)] px-5 py-3">
          <button
            type="button"
            onClick={() => {
              if (
                window.confirm(
                  `¿Borrar "${summary?.name}"? Esta acción no se puede deshacer.`,
                )
              ) {
                deleteMutation.mutate();
              }
            }}
            disabled={deleteMutation.isPending}
            className="rounded border border-red-300 bg-white px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
          >
            {deleteMutation.isPending ? "Borrando..." : "Borrar"}
          </button>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-[var(--color-border)] bg-white px-3 py-1.5 text-xs font-medium hover:bg-neutral-50"
            >
              Cerrar
            </button>
            <button
              type="button"
              onClick={() => {
                if (draft !== null) saveMutation.mutate(draft);
              }}
              disabled={!isDirty || saveMutation.isPending}
              className="rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {saveMutation.isPending ? "Guardando..." : "Guardar"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function IntegrationsTable({
  query,
}: {
  query: ReturnType<typeof useQuery<AdminIntegrationsResponse>>;
}) {
  if (query.isLoading) return <Loader />;
  if (query.isError)
    return <ErrorBlock message={(query.error as Error).message} />;
  const items = query.data?.integrations ?? [];
  if (items.length === 0) return <EmptyBlock label="integrations" />;
  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--color-border)] bg-white">
      <table className="w-full text-left text-xs">
        <thead className="bg-[var(--color-surface-fog)] text-[10px] uppercase tracking-wide text-neutral-500">
          <tr>
            <th className="px-3 py-2">Workspace</th>
            <th className="px-3 py-2">App</th>
            <th className="px-3 py-2">Provider</th>
            <th className="px-3 py-2">Estado</th>
            <th className="px-3 py-2">Creado</th>
          </tr>
        </thead>
        <tbody>
          {items.map((r) => (
            <tr key={r.id} className="border-t border-[var(--color-border)]">
              <td className="px-3 py-2">{r.workspace_name ?? "—"}</td>
              <td className="px-3 py-2 font-mono">{r.app}</td>
              <td className="px-3 py-2">{r.provider}</td>
              <td className="px-3 py-2">
                {r.status === "connected" ? (
                  <span className="text-emerald-600">{r.status}</span>
                ) : r.status === "pending" ? (
                  <span className="text-amber-600">{r.status}</span>
                ) : (
                  <span className="text-neutral-500">{r.status}</span>
                )}
              </td>
              <td className="px-3 py-2">
                {formatDistanceToNow(new Date(r.created_at), { addSuffix: true })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Helpers
// --------------------------------------------------------------------------- //

function Loader() {
  return <div className="text-sm text-neutral-500">Cargando…</div>;
}

function ErrorBlock({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
      {message}
    </div>
  );
}

function EmptyBlock({ label }: { label: string }) {
  return (
    <div className="rounded-lg border border-dashed border-[var(--color-border)] bg-white py-10 text-center text-sm text-neutral-500">
      No hay {label} todavía.
    </div>
  );
}
