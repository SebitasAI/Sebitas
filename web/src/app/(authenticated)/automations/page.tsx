"use client";

// Automations page. Sibling of /scheduled-tasks: same visual language
// (card + tabs + search + pause toggle + expandable detail with run
// history) adapted for source-driven hooks. Create / edit / delete
// live in the Slack chat tools; the web reads + pauses/resumes + can
// rotate the URL secret for source=direct.

import { useMemo, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import {
  ChevronDown,
  ChevronUp,
  Copy,
  KeyRound,
  Pause,
  Play,
  Search,
  Zap,
} from "lucide-react";

import { PageBody, PageHeader } from "../_components/page-header";
import {
  automationsApi,
  type Automation,
  type AutomationListFilter,
  type AutomationListResponse,
  type AutomationRun,
  type AutomationRunsResponse,
  type AutomationSource,
} from "@/lib/api/automations";

const AUTOMATIONS_QUERY_KEY = ["automations", "all"] as const;

export default function AutomationsPage() {
  return (
    <>
      <PageHeader title="Automations" Icon={Zap} />
      <PageBody>
        <AutomationsBody />
      </PageBody>
    </>
  );
}

function AutomationsBody() {
  const { getToken } = useAuth();
  const queryClient = useQueryClient();

  const listQuery = useQuery({
    queryKey: AUTOMATIONS_QUERY_KEY,
    queryFn: async (): Promise<AutomationListResponse> => {
      const token = await getToken({ template: "backend" });
      if (!token) {
        throw new Error("No Clerk session token available.");
      }
      return automationsApi.list("all", token);
    },
  });

  const all = useMemo(
    () => listQuery.data?.automations ?? [],
    [listQuery.data],
  );
  const mine = useMemo(
    () => all.filter((a) => a.scope === "local"),
    [all],
  );

  const [tab, setTab] = useState<AutomationListFilter>("mine");
  const [search, setSearch] = useState("");

  const visible = useMemo(() => {
    const pool = tab === "mine" ? mine : all;
    if (!search.trim()) return pool;
    const needle = search.trim().toLowerCase();
    return pool.filter(
      (a) =>
        a.name.toLowerCase().includes(needle) ||
        (a.description ?? "").toLowerCase().includes(needle) ||
        a.source.toLowerCase().includes(needle),
    );
  }, [tab, search, all, mine]);

  const counts = { all: all.length, mine: mine.length } as const;

  const pauseMut = useMutation({
    mutationFn: async (handle: string) => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return automationsApi.pause(handle, token);
    },
    onMutate: async (handle) => {
      await queryClient.cancelQueries({ queryKey: AUTOMATIONS_QUERY_KEY });
      const prev = queryClient.getQueryData<AutomationListResponse>(
        AUTOMATIONS_QUERY_KEY,
      );
      if (prev) {
        queryClient.setQueryData<AutomationListResponse>(
          AUTOMATIONS_QUERY_KEY,
          {
            ...prev,
            automations: prev.automations.map((a) =>
              a.id === handle || a.name === handle
                ? { ...a, is_paused: true }
                : a,
            ),
          },
        );
      }
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) {
        queryClient.setQueryData(AUTOMATIONS_QUERY_KEY, ctx.prev);
      }
    },
    onSuccess: (updated) => {
      queryClient.setQueryData<AutomationListResponse>(
        AUTOMATIONS_QUERY_KEY,
        (curr) =>
          curr
            ? {
                ...curr,
                automations: curr.automations.map((a) =>
                  a.id === updated.id ? updated : a,
                ),
              }
            : curr,
      );
    },
  });

  const resumeMut = useMutation({
    mutationFn: async (handle: string) => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return automationsApi.resume(handle, token);
    },
    onMutate: async (handle) => {
      await queryClient.cancelQueries({ queryKey: AUTOMATIONS_QUERY_KEY });
      const prev = queryClient.getQueryData<AutomationListResponse>(
        AUTOMATIONS_QUERY_KEY,
      );
      if (prev) {
        queryClient.setQueryData<AutomationListResponse>(
          AUTOMATIONS_QUERY_KEY,
          {
            ...prev,
            automations: prev.automations.map((a) =>
              a.id === handle || a.name === handle
                ? { ...a, is_paused: false }
                : a,
            ),
          },
        );
      }
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) {
        queryClient.setQueryData(AUTOMATIONS_QUERY_KEY, ctx.prev);
      }
    },
    onSuccess: (updated) => {
      queryClient.setQueryData<AutomationListResponse>(
        AUTOMATIONS_QUERY_KEY,
        (curr) =>
          curr
            ? {
                ...curr,
                automations: curr.automations.map((a) =>
                  a.id === updated.id ? updated : a,
                ),
              }
            : curr,
      );
    },
  });

  const rotateMut = useMutation({
    mutationFn: async (handle: string) => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return automationsApi.rotateUrl(handle, token);
    },
    onSuccess: (updated) => {
      queryClient.setQueryData<AutomationListResponse>(
        AUTOMATIONS_QUERY_KEY,
        (curr) =>
          curr
            ? {
                ...curr,
                automations: curr.automations.map((a) =>
                  a.id === updated.id ? updated : a,
                ),
              }
            : curr,
      );
    },
  });

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm text-neutral-500">
        Las automations disparan al agente cuando llega un evento externo.
        Tres tipos: URL directa (POSTeá JSON desde donde quieras), o vía
        Pipedream / Composio para conectar apps con OAuth + filtros.
        Se crean y editan hablándole a Misterr por chat.
      </p>

      <SearchBar value={search} onChange={setSearch} />

      <Tabs value={tab} counts={counts} onChange={setTab} />

      {listQuery.isLoading ? (
        <SkeletonList />
      ) : listQuery.isError ? (
        <ErrorState
          message={(listQuery.error as Error)?.message ?? "Error desconocido"}
          onRetry={() => listQuery.refetch()}
        />
      ) : visible.length === 0 ? (
        <EmptyState tab={tab} search={search} />
      ) : (
        <ul className="flex flex-col gap-3">
          {visible.map((a) => (
            <AutomationCard
              key={a.id}
              automation={a}
              onPause={() => pauseMut.mutate(a.id)}
              onResume={() => resumeMut.mutate(a.id)}
              onRotate={() => rotateMut.mutate(a.id)}
              isRotating={
                rotateMut.isPending && rotateMut.variables === a.id
              }
            />
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Subcomponents
// ---------------------------------------------------------------------------

function SearchBar({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="relative block">
      <Search
        className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-neutral-400"
        strokeWidth={1.75}
      />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Buscar por nombre, descripción o source"
        className="w-full rounded-lg border border-[var(--color-border)] bg-white py-2 pl-9 pr-3 text-sm text-[var(--color-ink-deep)] placeholder:text-neutral-400 focus:border-[#FF5200] focus:outline-none focus:ring-1 focus:ring-[#FF5200]/30"
      />
    </label>
  );
}

function Tabs({
  value,
  counts,
  onChange,
}: {
  value: AutomationListFilter;
  counts: { all: number; mine: number };
  onChange: (v: AutomationListFilter) => void;
}) {
  const items: { id: AutomationListFilter; label: string; count: number }[] = [
    { id: "all", label: "All", count: counts.all },
    { id: "mine", label: "Mine", count: counts.mine },
  ];
  return (
    <div
      role="tablist"
      className="flex gap-1 rounded-lg bg-[var(--color-surface-fog)] p-1 text-sm"
    >
      {items.map((it) => {
        const active = it.id === value;
        return (
          <button
            key={it.id}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(it.id)}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-colors ${
              active
                ? "bg-white text-[var(--color-ink-deep)] shadow-sm"
                : "text-neutral-600 hover:text-[var(--color-ink-deep)]"
            }`}
          >
            <span>{it.label}</span>
            <span
              className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${
                active
                  ? "bg-[#FF5200]/15 text-[#FF5200]"
                  : "bg-white text-neutral-500"
              }`}
            >
              {it.count}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function SkeletonList() {
  return (
    <ul className="flex flex-col gap-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <li
          key={i}
          className="h-24 animate-pulse rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-fog)]"
        />
      ))}
    </ul>
  );
}

function EmptyState({
  tab,
  search,
}: {
  tab: AutomationListFilter;
  search: string;
}) {
  let message: string;
  if (search.trim()) {
    message = `No hay automations que matcheen "${search}".`;
  } else if (tab === "mine") {
    message =
      "Todavía no creaste automations. Pedile a Misterr en Slack: \"creá una automation con source direct\" para empezar.";
  } else {
    message = "No hay automations en este workspace todavía.";
  }
  return (
    <div className="rounded-lg border border-dashed border-[var(--color-border)] bg-white py-10 text-center text-sm text-neutral-500">
      {message}
    </div>
  );
}

function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
      <div className="font-medium">No pude cargar las automations.</div>
      <div className="mt-1 text-xs text-red-600">{message}</div>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 rounded-md border border-red-300 bg-white px-3 py-1 text-xs font-medium text-red-700 hover:bg-red-100"
      >
        Reintentar
      </button>
    </div>
  );
}

function AutomationCard({
  automation,
  onPause,
  onResume,
  onRotate,
  isRotating,
}: {
  automation: Automation;
  onPause: () => void;
  onResume: () => void;
  onRotate: () => void;
  isRotating: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const { getToken } = useAuth();

  const runsQuery = useQuery({
    queryKey: ["automations", "runs", automation.id],
    enabled: expanded,
    queryFn: async (): Promise<AutomationRunsResponse> => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return automationsApi.runs(automation.id, token);
    },
  });

  const lastFireRelative = automation.last_fired_at
    ? formatDistanceToNow(new Date(automation.last_fired_at), {
        addSuffix: true,
      })
    : "nunca";

  const createdRelative = formatDistanceToNow(
    new Date(automation.created_at),
    { addSuffix: true },
  );

  const metadataEntries = Object.entries(automation.trigger_metadata ?? {});

  return (
    <li className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-white">
      <header className="flex items-start justify-between gap-3 px-4 py-3">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex flex-1 items-start gap-2 text-left"
          aria-expanded={expanded}
        >
          <span className="mt-0.5 text-neutral-400">
            {expanded ? (
              <ChevronUp className="size-4" strokeWidth={1.75} />
            ) : (
              <ChevronDown className="size-4" strokeWidth={1.75} />
            )}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium text-[var(--color-ink-deep)]">
                {automation.name}
              </span>
              <SourceBadge source={automation.source} />
              {automation.is_paused ? <PausedBadge /> : null}
              {automation.last_fire_status === "failed" ? (
                <FailedBadge />
              ) : null}
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-1 text-xs text-neutral-500">
              <span>
                {automation.fire_count}{" "}
                {automation.fire_count === 1 ? "fire" : "fires"}
              </span>
              <span className="text-neutral-400">·</span>
              <span>last: {lastFireRelative}</span>
              {automation.last_fire_status ? (
                <FireStatusDot status={automation.last_fire_status} />
              ) : null}
            </div>
          </div>
        </button>
        <PauseToggle
          isPaused={automation.is_paused}
          onPause={onPause}
          onResume={onResume}
        />
      </header>

      {expanded ? (
        <div className="border-t border-[var(--color-border)] bg-[var(--color-surface-fog)]/40 px-4 py-3 text-xs text-neutral-600">
          {automation.description ? (
            <p className="mb-3 text-[13px] leading-relaxed text-neutral-700">
              {automation.description}
            </p>
          ) : null}

          {automation.source === "direct" && automation.webhook_url ? (
            <DirectWebhookPanel
              url={automation.webhook_url}
              onRotate={onRotate}
              isRotating={isRotating}
            />
          ) : null}

          {automation.source !== "direct" && automation.external_trigger_id ? (
            <Field label="Upstream trigger id">
              <span className="font-mono text-[11px]">
                {automation.external_trigger_id}
              </span>
            </Field>
          ) : null}

          <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-3">
            <Field label="Created">{createdRelative}</Field>
            <Field label="Destination">
              {automation.destination_channel ? (
                <span className="font-mono">
                  {automation.destination_channel}
                </span>
              ) : (
                <span className="italic">DM al creador (default)</span>
              )}
            </Field>
            <Field label="Source">{automation.source}</Field>
          </dl>

          <div className="mt-3 border-t border-[var(--color-border)] pt-3">
            <div className="text-[10px] font-medium uppercase tracking-wide text-neutral-500">
              Prompt template
            </div>
            <p className="mt-1 whitespace-pre-wrap text-[13px] leading-relaxed text-neutral-700">
              {automation.prompt_template}
            </p>
          </div>

          {metadataEntries.length > 0 ? (
            <div className="mt-3 border-t border-[var(--color-border)] pt-3">
              <div className="text-[10px] font-medium uppercase tracking-wide text-neutral-500">
                Trigger metadata
              </div>
              <ul className="mt-1 flex flex-wrap gap-1.5">
                {metadataEntries.map(([k, v]) => (
                  <li
                    key={k}
                    className="rounded bg-white px-1.5 py-0.5 font-mono text-[11px] text-neutral-700"
                  >
                    {k}={" "}
                    {typeof v === "string" ? v : JSON.stringify(v)}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {automation.last_fire_error ? (
            <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-2 text-[11px] text-red-700">
              <span className="font-medium">Último error:</span>{" "}
              {automation.last_fire_error}
            </div>
          ) : null}

          <RunHistoryPanel
            runs={runsQuery.data?.runs}
            isLoading={runsQuery.isLoading}
            isError={runsQuery.isError}
          />
        </div>
      ) : null}
    </li>
  );
}

function DirectWebhookPanel({
  url,
  onRotate,
  isRotating,
}: {
  url: string;
  onRotate: () => void;
  isRotating: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Some browsers disallow clipboard write without focus; ignore.
    }
  };
  return (
    <div className="rounded-md border border-[#FF5200]/20 bg-[#FF5200]/5 p-3">
      <div className="text-[10px] font-medium uppercase tracking-wide text-[#FF5200]">
        Webhook URL (mantenelo secreto)
      </div>
      <div className="mt-1 flex items-center gap-2">
        <code className="flex-1 break-all rounded bg-white px-2 py-1 font-mono text-[11px] text-neutral-800">
          {url}
        </code>
        <button
          type="button"
          onClick={onCopy}
          className="inline-flex items-center gap-1 rounded-md border border-[var(--color-border)] bg-white px-2 py-1 text-[11px] text-neutral-700 hover:bg-[var(--color-surface-fog)]"
          title="Copiar al portapapeles"
        >
          <Copy className="size-3" strokeWidth={1.75} />
          {copied ? "copiado" : "copiar"}
        </button>
        <button
          type="button"
          onClick={onRotate}
          disabled={isRotating}
          className="inline-flex items-center gap-1 rounded-md border border-[var(--color-border)] bg-white px-2 py-1 text-[11px] text-neutral-700 hover:bg-[var(--color-surface-fog)] disabled:opacity-50"
          title="Regenerar URL (la anterior deja de funcionar)"
        >
          <KeyRound className="size-3" strokeWidth={1.75} />
          {isRotating ? "rotando…" : "rotar"}
        </button>
      </div>
      <p className="mt-1 text-[10px] text-neutral-500">
        Cualquiera con esta URL puede disparar la automation. Si se filtra,
        rotala.
      </p>
    </div>
  );
}

function RunHistoryPanel({
  runs,
  isLoading,
  isError,
}: {
  runs: AutomationRun[] | undefined;
  isLoading: boolean;
  isError: boolean;
}) {
  return (
    <div className="mt-3 border-t border-[var(--color-border)] pt-3">
      <div className="text-[10px] font-medium uppercase tracking-wide text-neutral-500">
        Historial de fires
      </div>
      {isLoading ? (
        <div className="mt-1 text-[11px] text-neutral-500">Cargando…</div>
      ) : isError ? (
        <div className="mt-1 text-[11px] text-red-600">
          No pude cargar el historial.
        </div>
      ) : !runs || runs.length === 0 ? (
        <div className="mt-1 text-[11px] text-neutral-500">
          Sin fires registrados todavía.
        </div>
      ) : (
        <ul className="mt-1 flex flex-col gap-2">
          {runs.map((run) => (
            <RunItem key={run.id} run={run} />
          ))}
        </ul>
      )}
    </div>
  );
}

function RunItem({ run }: { run: AutomationRun }) {
  const [open, setOpen] = useState(false);
  const startedRelative = formatDistanceToNow(new Date(run.started_at), {
    addSuffix: true,
  });
  const dot =
    run.status === "success"
      ? "bg-emerald-500"
      : run.status === "failed"
        ? "bg-red-500"
        : run.status === "skipped"
          ? "bg-neutral-400"
          : "bg-amber-500";
  return (
    <li className="rounded-md border border-[var(--color-border)] bg-white">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-2 py-1.5 text-left"
      >
        <div className="flex items-center gap-2">
          <span className={`inline-block size-1.5 rounded-full ${dot}`} />
          <span className="text-[11px] font-medium text-neutral-700">
            {run.status}
          </span>
          <span className="text-[11px] text-neutral-500">
            {startedRelative}
          </span>
        </div>
        <span className="text-neutral-400">
          {open ? (
            <ChevronUp className="size-3.5" strokeWidth={1.75} />
          ) : (
            <ChevronDown className="size-3.5" strokeWidth={1.75} />
          )}
        </span>
      </button>
      {open ? (
        <div className="border-t border-[var(--color-border)] px-2 py-2 text-[11px] text-neutral-700">
          {run.rendered_prompt ? (
            <details className="mb-1" open>
              <summary className="cursor-pointer text-neutral-500">
                rendered prompt
              </summary>
              <pre className="mt-1 whitespace-pre-wrap font-sans text-[11px] text-neutral-700">
                {run.rendered_prompt}
              </pre>
            </details>
          ) : null}
          {run.output ? (
            <pre className="whitespace-pre-wrap font-sans leading-relaxed">
              {run.output}
            </pre>
          ) : run.error ? (
            <pre className="whitespace-pre-wrap font-sans leading-relaxed text-red-700">
              {run.error}
            </pre>
          ) : null}
          <details className="mt-2 text-[10px] text-neutral-500">
            <summary className="cursor-pointer">trigger payload</summary>
            <pre className="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap font-mono text-[10px] text-neutral-600">
              {JSON.stringify(run.trigger_payload, null, 2)}
            </pre>
          </details>
        </div>
      ) : null}
    </li>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-[10px] font-medium uppercase tracking-wide text-neutral-500">
        {label}
      </dt>
      <dd className="flex items-center gap-1.5 text-[12px] text-neutral-800">
        {children}
      </dd>
    </div>
  );
}

function SourceBadge({ source }: { source: AutomationSource }) {
  const cls =
    source === "direct"
      ? "bg-[#FF5200]/10 text-[#FF5200]"
      : source === "pipedream"
        ? "bg-sky-100 text-sky-700"
        : "bg-violet-100 text-violet-700";
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${cls}`}
    >
      {source}
    </span>
  );
}

function PausedBadge() {
  return (
    <span className="inline-flex items-center rounded-full bg-yellow-100 px-2 py-0.5 text-[10px] font-medium text-yellow-700">
      Pausada
    </span>
  );
}

function FailedBadge() {
  return (
    <span className="inline-flex items-center rounded-full bg-red-100 px-2 py-0.5 text-[10px] font-medium text-red-700">
      Last fire failed
    </span>
  );
}

function FireStatusDot({
  status,
}: {
  status: NonNullable<Automation["last_fire_status"]>;
}) {
  const color =
    status === "success"
      ? "bg-emerald-500"
      : status === "failed"
        ? "bg-red-500"
        : "bg-neutral-400";
  return (
    <span
      className={`inline-block size-1.5 rounded-full ${color}`}
      title={status}
      aria-label={status}
    />
  );
}

function PauseToggle({
  isPaused,
  onPause,
  onResume,
}: {
  isPaused: boolean;
  onPause: () => void;
  onResume: () => void;
}) {
  if (isPaused) {
    return (
      <button
        type="button"
        onClick={onResume}
        className="inline-flex size-8 shrink-0 items-center justify-center rounded-md border border-[var(--color-border)] bg-white text-neutral-600 transition-colors hover:bg-[var(--color-surface-fog)] hover:text-[#FF5200]"
        aria-label="Reanudar automation"
        title="Reanudar"
      >
        <Play className="size-4" strokeWidth={1.75} />
      </button>
    );
  }
  return (
    <button
      type="button"
      onClick={onPause}
      className="inline-flex size-8 shrink-0 items-center justify-center rounded-md border border-[var(--color-border)] bg-white text-neutral-600 transition-colors hover:bg-[var(--color-surface-fog)] hover:text-[#FF5200]"
      aria-label="Pausar automation"
      title="Pausar"
    >
      <Pause className="size-4" strokeWidth={1.75} />
    </button>
  );
}
