"use client";

// Scheduled Tasks page (slice T-2). Read + pause/resume only -- creation,
// edit, and deletion live in the Slack chat tools. The card visual is a
// trimmed take on Antiff's PlatformCard: same color tokens, same border
// + rounded corners, but compact rows since each card is a status surface
// rather than a navigable destination.

import { useMemo, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import {
  CalendarClock,
  ChevronDown,
  ChevronUp,
  Pause,
  Play,
  Search,
} from "lucide-react";
import cronstrue from "cronstrue";

import { PageBody, PageHeader } from "../_components/page-header";
import {
  scheduledTasksApi,
  type ListFilter,
  type ScheduledTask,
  type TaskListResponse,
} from "@/lib/api/scheduled-tasks";

const TASKS_QUERY_KEY = ["scheduled-tasks", "all"] as const;

export default function ScheduledTasksPage() {
  return (
    <>
      <PageHeader title="Scheduled tasks" Icon={CalendarClock} />
      <PageBody>
        <ScheduledTasksBody />
      </PageBody>
    </>
  );
}

function ScheduledTasksBody() {
  const { getToken } = useAuth();
  const queryClient = useQueryClient();

  const tasksQuery = useQuery({
    queryKey: TASKS_QUERY_KEY,
    queryFn: async (): Promise<TaskListResponse> => {
      // Use the "backend" JWT template configured in Clerk dashboard --
      // the default session token doesn't carry `email`, which our backend
      // needs to map the Clerk identity to an internal AppUser. The
      // template must exist in Clerk dashboard with at least:
      //   { "email": "{{user.primary_email_address}}" }
      const token = await getToken({ template: "backend" });
      if (!token) {
        throw new Error("No Clerk session token available.");
      }
      return scheduledTasksApi.list("all", token);
    },
  });

  // Memoize the array reference itself: `data?.tasks ?? []` creates a new
  // empty literal on every render which would invalidate every downstream
  // useMemo dep. Keying off tasksQuery.data fixes that.
  const allTasks = useMemo(
    () => tasksQuery.data?.tasks ?? [],
    [tasksQuery.data],
  );
  const mine = useMemo(
    () => allTasks.filter((t) => t.scope === "local"),
    [allTasks],
  );
  const system = useMemo(
    () => allTasks.filter((t) => t.scope === "system"),
    [allTasks],
  );

  // Default tab: prefer "mine" when the user has any of their own; fall back
  // to "system" so a brand-new user still sees the seeded default tasks.
  const defaultTab: ListFilter = mine.length > 0 ? "mine" : "system";
  const [tab, setTab] = useState<ListFilter>(defaultTab);
  const [search, setSearch] = useState("");

  // After first load, snap to the right default once the data lands. This
  // runs only when the data first transitions from empty to populated.
  useMemoEffect(
    () => {
      if (tasksQuery.data && tab === "system" && mine.length > 0) {
        setTab("mine");
      }
    },
    [tasksQuery.data, mine.length],
  );

  const visible = useMemo(() => {
    const pool =
      tab === "all" ? allTasks : tab === "mine" ? mine : system;
    if (!search.trim()) return pool;
    const needle = search.trim().toLowerCase();
    return pool.filter((t) => t.name.toLowerCase().includes(needle));
  }, [tab, search, allTasks, mine, system]);

  const counts = {
    all: allTasks.length,
    mine: mine.length,
    system: system.length,
  } as const;

  const pauseMut = useMutation({
    mutationFn: async (vars: { idOrName: string; until: string | null }) => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return scheduledTasksApi.pause(vars.idOrName, vars.until, token);
    },
    onMutate: async (vars) => {
      // Optimistic: flip is_paused immediately so the UI is responsive even
      // on a slow API.
      await queryClient.cancelQueries({ queryKey: TASKS_QUERY_KEY });
      const prev =
        queryClient.getQueryData<TaskListResponse>(TASKS_QUERY_KEY);
      if (prev) {
        queryClient.setQueryData<TaskListResponse>(TASKS_QUERY_KEY, {
          ...prev,
          tasks: prev.tasks.map((t) =>
            t.name === vars.idOrName || t.id === vars.idOrName
              ? { ...t, is_paused: true, paused_until: vars.until }
              : t,
          ),
        });
      }
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) {
        queryClient.setQueryData(TASKS_QUERY_KEY, ctx.prev);
      }
    },
    onSuccess: (updated) => {
      queryClient.setQueryData<TaskListResponse>(TASKS_QUERY_KEY, (curr) =>
        curr
          ? {
              ...curr,
              tasks: curr.tasks.map((t) =>
                t.id === updated.id ? updated : t,
              ),
            }
          : curr,
      );
    },
  });

  const resumeMut = useMutation({
    mutationFn: async (idOrName: string) => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return scheduledTasksApi.resume(idOrName, token);
    },
    onMutate: async (idOrName) => {
      await queryClient.cancelQueries({ queryKey: TASKS_QUERY_KEY });
      const prev =
        queryClient.getQueryData<TaskListResponse>(TASKS_QUERY_KEY);
      if (prev) {
        queryClient.setQueryData<TaskListResponse>(TASKS_QUERY_KEY, {
          ...prev,
          tasks: prev.tasks.map((t) =>
            t.name === idOrName || t.id === idOrName
              ? { ...t, is_paused: false, paused_until: null }
              : t,
          ),
        });
      }
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) {
        queryClient.setQueryData(TASKS_QUERY_KEY, ctx.prev);
      }
    },
    onSuccess: (updated) => {
      queryClient.setQueryData<TaskListResponse>(TASKS_QUERY_KEY, (curr) =>
        curr
          ? {
              ...curr,
              tasks: curr.tasks.map((t) =>
                t.id === updated.id ? updated : t,
              ),
            }
          : curr,
      );
    },
  });

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm text-neutral-500">
        Las tasks se crean o modifican hablándole a Misterr por chat. Acá podés
        verlas y pausarlas / reanudarlas.
      </p>

      <SearchBar value={search} onChange={setSearch} />

      <Tabs value={tab} counts={counts} onChange={setTab} />

      {tasksQuery.isLoading ? (
        <SkeletonList />
      ) : tasksQuery.isError ? (
        <ErrorState
          message={(tasksQuery.error as Error)?.message ?? "Error desconocido"}
          onRetry={() => tasksQuery.refetch()}
        />
      ) : visible.length === 0 ? (
        <EmptyState tab={tab} search={search} />
      ) : (
        <ul className="flex flex-col gap-3">
          {visible.map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              onPause={(until) =>
                pauseMut.mutate({ idOrName: task.id, until })
              }
              onResume={() => resumeMut.mutate(task.id)}
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
        placeholder="Buscar tasks por nombre"
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
  value: ListFilter;
  counts: { all: number; mine: number; system: number };
  onChange: (v: ListFilter) => void;
}) {
  const items: { id: ListFilter; label: string; count: number }[] = [
    { id: "all", label: "All", count: counts.all },
    { id: "mine", label: "My tasks", count: counts.mine },
    { id: "system", label: "System", count: counts.system },
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
  tab: ListFilter;
  search: string;
}) {
  let message: string;
  if (search.trim()) {
    message = `No hay tasks que matcheen "${search}".`;
  } else if (tab === "mine") {
    message = "Todavía no creaste tasks. Decile a Misterr en Slack que arme una.";
  } else if (tab === "system") {
    message =
      "No hay system tasks configuradas en este workspace. Esto es raro; el seeder debería haberlas creado al instalar el bot.";
  } else {
    message = "No hay tasks en este workspace todavía.";
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
      <div className="font-medium">No pude cargar las scheduled tasks.</div>
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

function TaskCard({
  task,
  onPause,
  onResume,
}: {
  task: ScheduledTask;
  onPause: (until: string | null) => void;
  onResume: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  const cronHuman = useMemo(() => {
    try {
      return cronstrue.toString(task.cron_spec, {
        use24HourTimeFormat: false,
        dayOfWeekStartIndexZero: true,
      });
    } catch {
      return task.cron_spec;
    }
  }, [task.cron_spec]);

  const lastRunRelative = task.last_run_at
    ? formatDistanceToNow(new Date(task.last_run_at), { addSuffix: true })
    : "nunca";

  const createdRelative = formatDistanceToNow(new Date(task.created_at), {
    addSuffix: true,
  });

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
                {task.name}
              </span>
              <ScopeBadge scope={task.scope} />
              {task.is_paused ? <PausedBadge until={task.paused_until} /> : null}
            </div>
            <div className="mt-0.5 text-xs text-neutral-500">
              <span className="font-medium text-neutral-700">⏰ {cronHuman}</span>
              <span className="ml-1.5 text-neutral-400">· {task.timezone}</span>
            </div>
          </div>
        </button>
        <PauseToggle
          isPaused={task.is_paused}
          onPause={onPause}
          onResume={onResume}
        />
      </header>

      {expanded ? (
        <div className="border-t border-[var(--color-border)] bg-[var(--color-surface-fog)]/40 px-4 py-3 text-xs text-neutral-600">
          <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-3">
            <Field label="Last run">
              {lastRunRelative}
              {task.last_run_status ? (
                <RunStatusDot status={task.last_run_status} />
              ) : null}
            </Field>
            <Field label="Created">{createdRelative}</Field>
            <Field label="Model">Team default</Field>
          </dl>

          <div className="mt-3 border-t border-[var(--color-border)] pt-3">
            <div className="text-[10px] font-medium uppercase tracking-wide text-neutral-500">
              Prompt
            </div>
            <p className="mt-1 whitespace-pre-wrap text-[13px] leading-relaxed text-neutral-700">
              {task.prompt}
            </p>
          </div>

          {task.last_run_error ? (
            <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-2 text-[11px] text-red-700">
              <span className="font-medium">Último error:</span>{" "}
              {task.last_run_error}
            </div>
          ) : null}
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

function ScopeBadge({ scope }: { scope: ScheduledTask["scope"] }) {
  const label =
    scope === "system"
      ? "System task"
      : scope === "global"
        ? "Global"
        : "My task";
  const cls =
    scope === "system"
      ? "bg-neutral-100 text-neutral-600"
      : scope === "global"
        ? "bg-amber-100 text-amber-700"
        : "bg-[#FF5200]/10 text-[#FF5200]";
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${cls}`}
    >
      {label}
    </span>
  );
}

function PausedBadge({ until }: { until: string | null }) {
  return (
    <span className="inline-flex items-center rounded-full bg-yellow-100 px-2 py-0.5 text-[10px] font-medium text-yellow-700">
      {until ? `Pausada hasta ${until.slice(0, 10)}` : "Pausada"}
    </span>
  );
}

function RunStatusDot({
  status,
}: {
  status: NonNullable<ScheduledTask["last_run_status"]>;
}) {
  const color =
    status === "success"
      ? "bg-emerald-500"
      : status === "failed"
        ? "bg-red-500"
        : "bg-amber-500";
  return (
    <span
      className={`inline-block size-1.5 rounded-full ${color}`}
      title={status}
      aria-label={status}
    />
  );
}

// Pause button -> opens a tiny inline menu with two options. We keep it
// inline (no portal) since the parent <li> has overflow-hidden but the menu
// is small and sits to the left of the button -- collisions are unlikely
// even for the last task in the list.
function PauseToggle({
  isPaused,
  onPause,
  onResume,
}: {
  isPaused: boolean;
  onPause: (until: string | null) => void;
  onResume: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"menu" | "until">("menu");
  const [date, setDate] = useState<string>("");

  if (isPaused) {
    return (
      <button
        type="button"
        onClick={onResume}
        className="inline-flex size-8 shrink-0 items-center justify-center rounded-md border border-[var(--color-border)] bg-white text-neutral-600 transition-colors hover:bg-[var(--color-surface-fog)] hover:text-[#FF5200]"
        aria-label="Reanudar task"
        title="Reanudar"
      >
        <Play className="size-4" strokeWidth={1.75} />
      </button>
    );
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => {
          setOpen((v) => !v);
          setMode("menu");
        }}
        className="inline-flex size-8 shrink-0 items-center justify-center rounded-md border border-[var(--color-border)] bg-white text-neutral-600 transition-colors hover:bg-[var(--color-surface-fog)] hover:text-[#FF5200]"
        aria-label="Pausar task"
        aria-expanded={open}
        title="Pausar"
      >
        <Pause className="size-4" strokeWidth={1.75} />
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 top-9 z-30 w-56 rounded-md border border-[var(--color-border)] bg-white shadow-lg"
          // Prevent the surrounding card's expand/collapse from firing when
          // the user clicks inside the menu.
          onClick={(e) => e.stopPropagation()}
        >
          {mode === "menu" ? (
            <div className="py-1 text-sm">
              <button
                type="button"
                className="block w-full px-3 py-2 text-left text-[var(--color-ink-deep)] hover:bg-[var(--color-surface-fog)]"
                onClick={() => {
                  onPause(null);
                  setOpen(false);
                }}
              >
                Pausar indefinidamente
              </button>
              <button
                type="button"
                className="block w-full px-3 py-2 text-left text-[var(--color-ink-deep)] hover:bg-[var(--color-surface-fog)]"
                onClick={() => setMode("until")}
              >
                Pausar hasta…
              </button>
            </div>
          ) : (
            <div className="flex flex-col gap-2 p-3 text-sm">
              <label className="flex flex-col gap-1 text-xs text-neutral-600">
                Reanudar el
                <input
                  type="date"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  min={new Date().toISOString().slice(0, 10)}
                  className="rounded border border-[var(--color-border)] bg-white px-2 py-1 text-sm text-[var(--color-ink-deep)]"
                />
              </label>
              <div className="flex items-center justify-end gap-2">
                <button
                  type="button"
                  className="rounded px-2 py-1 text-xs text-neutral-500 hover:text-[var(--color-ink-deep)]"
                  onClick={() => {
                    setOpen(false);
                    setMode("menu");
                    setDate("");
                  }}
                >
                  Cancelar
                </button>
                <button
                  type="button"
                  disabled={!date}
                  className="rounded bg-[#FF5200] px-3 py-1 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={() => {
                    if (date) {
                      onPause(date);
                      setOpen(false);
                      setMode("menu");
                      setDate("");
                    }
                  }}
                >
                  Pausar
                </button>
              </div>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}

// useMemoEffect: like useEffect but only re-runs when the SECOND deps change.
// We use it to snap the tab to "mine" exactly once when the data arrives;
// using useEffect directly would re-fire on every render of `tab` itself
// and create a tab-switch ping-pong. (Implemented inline to avoid pulling
// in another tiny dep.)
import { useEffect, useRef } from "react";
function useMemoEffect(fn: () => void, deps: unknown[]) {
  const lastDeps = useRef<unknown[] | null>(null);
  useEffect(() => {
    const last = lastDeps.current;
    if (last === null || deps.some((d, i) => d !== last[i])) {
      lastDeps.current = deps;
      fn();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
