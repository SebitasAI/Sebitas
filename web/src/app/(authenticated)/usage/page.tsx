"use client";

// Usage dashboard. 4 tabs (Overview / Team / Activity / Scheduled Tasks)
// over the agent_run table. Read-only; the API is also read-only.
//
// Visual language mirrors /scheduled-tasks and /automations: same
// header, tabs, table, surface. No external chart library -- the
// stacked bar chart is a small inline SVG, sized to fit the card.

import { useMemo, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { BarChart3, ChevronDown, Search } from "lucide-react";

import { PageBody, PageHeader } from "../_components/page-header";
import {
  usageApi,
  type ActivityKindFilter,
  type ActivityResponse,
  type DateRange,
  type OverviewResponse,
  type ScheduledTaskResponse,
  type TeamResponse,
} from "@/lib/api/usage";

type Tab = "overview" | "team" | "activity" | "scheduled_tasks";

export default function UsagePage() {
  return (
    <>
      <PageHeader title="Usage" Icon={BarChart3} />
      <PageBody>
        <UsageBody />
      </PageBody>
    </>
  );
}

function UsageBody() {
  const [tab, setTab] = useState<Tab>("overview");
  const [range, setRange] = useState<DateRange>("30d");

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-between">
        <Tabs value={tab} onChange={setTab} />
        <RangeSelector value={range} onChange={setRange} />
      </div>

      {tab === "overview" ? <OverviewTab range={range} /> : null}
      {tab === "team" ? <TeamTab range={range} /> : null}
      {tab === "activity" ? <ActivityTab range={range} /> : null}
      {tab === "scheduled_tasks" ? <ScheduledTasksTab range={range} /> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared header bits
// ---------------------------------------------------------------------------

function Tabs({
  value,
  onChange,
}: {
  value: Tab;
  onChange: (v: Tab) => void;
}) {
  const items: { id: Tab; label: string }[] = [
    { id: "overview", label: "Overview" },
    { id: "team", label: "Team" },
    { id: "activity", label: "Activity" },
    { id: "scheduled_tasks", label: "Scheduled Tasks" },
  ];
  return (
    <div role="tablist" className="flex gap-1 text-sm">
      {items.map((it) => {
        const active = it.id === value;
        return (
          <button
            key={it.id}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(it.id)}
            className={`rounded-md px-3 py-1.5 transition-colors ${
              active
                ? "bg-[var(--color-surface-fog)] text-[var(--color-ink-deep)] font-medium"
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

function RangeSelector({
  value,
  onChange,
}: {
  value: DateRange;
  onChange: (v: DateRange) => void;
}) {
  const labels: Record<DateRange, string> = {
    "7d": "Last 7 days",
    "30d": "Last 30 days",
    "90d": "Last 90 days",
    all: "All time",
  };
  return (
    <label className="relative inline-flex items-center">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as DateRange)}
        className="appearance-none rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 pr-8 text-sm text-[var(--color-ink-deep)] focus:border-[#FF5200] focus:outline-none"
      >
        {(Object.keys(labels) as DateRange[]).map((k) => (
          <option key={k} value={k}>
            {labels[k]}
          </option>
        ))}
      </select>
      <ChevronDown
        className="pointer-events-none absolute right-2 size-4 text-neutral-500"
        strokeWidth={1.75}
      />
    </label>
  );
}

// ---------------------------------------------------------------------------
// Overview tab
// ---------------------------------------------------------------------------

const CATEGORY_COLORS: Record<string, string> = {
  threads: "#A6A0F1",
  scheduled_tasks: "#7E76E1",
  automations: "#FF5200",
  media: "#5DD9C7",
};

function OverviewTab({ range }: { range: DateRange }) {
  const { getToken } = useAuth();
  const q = useQuery({
    queryKey: ["usage", "overview", range],
    queryFn: async (): Promise<OverviewResponse> => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return usageApi.overview(range, token);
    },
  });

  if (q.isLoading) return <SkeletonOverview />;
  if (q.isError)
    return (
      <ErrorState
        message={(q.error as Error)?.message ?? "Error desconocido"}
        onRetry={() => q.refetch()}
      />
    );
  if (!q.data) return null;

  const data = q.data;
  return (
    <div className="flex flex-col gap-4">
      {/* Top stats card */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <StatCard label="TOTAL SPEND" value={fmtNumber(data.total_credits)} unit="credits" />
        <StatCard
          label="BURN"
          value={fmtNumber(data.burn_per_day)}
          unit="credits / day"
        />
      </div>

      {/* Credit usage chart */}
      <div className="rounded-lg border border-[var(--color-border)] bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-[var(--color-ink-deep)]">
              Credit usage
            </h2>
            <p className="text-xs text-neutral-500">
              See where your workspace spent credits over the selected range
            </p>
          </div>
          <CategoryLegend pct={data.category_pct} />
        </div>
        <div className="mt-4">
          <StackedBarChart daily={data.daily} />
        </div>
      </div>

      {/* Top users + Top scheduled tasks */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <TopList
          title="Top users"
          subtitle="Workspace members using the most credits"
          rows={data.top_users.map((u) => ({
            id: u.app_user_id ?? "system",
            label: u.display_name,
            credits: u.credits,
          }))}
        />
        <TopList
          title="Top scheduled tasks"
          subtitle="Recurring tasks using the most credits"
          rows={data.top_scheduled_tasks.map((t) => ({
            id: t.task_id ?? "unknown",
            label: t.name,
            credits: t.credits,
          }))}
        />
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  unit,
}: {
  label: string;
  value: string;
  unit: string;
}) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-white p-5">
      <div className="text-[10px] font-medium uppercase tracking-wide text-neutral-500">
        {label}
      </div>
      <div className="mt-1 flex items-baseline gap-1.5">
        <span className="text-2xl font-semibold tabular-nums text-[var(--color-ink-deep)]">
          {value}
        </span>
        <span className="text-xs text-neutral-500">{unit}</span>
      </div>
    </div>
  );
}

function CategoryLegend({ pct }: { pct: Record<string, number> }) {
  // Only show categories that have meaningful share.
  const entries = (Object.entries(pct) as [string, number][])
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1]);
  return (
    <div className="flex flex-wrap items-center gap-3 text-xs text-neutral-600">
      {entries.map(([k, v]) => (
        <span key={k} className="inline-flex items-center gap-1.5">
          <span
            className="inline-block size-2 rounded-full"
            style={{ background: CATEGORY_COLORS[k] ?? "#888" }}
          />
          {labelForCategory(k)} - {v}%
        </span>
      ))}
    </div>
  );
}

function labelForCategory(k: string): string {
  return (
    {
      threads: "Threads",
      scheduled_tasks: "Scheduled tasks",
      automations: "Automations",
      media: "Media generation",
    } as Record<string, string>
  )[k] ?? k;
}

function StackedBarChart({
  daily,
}: {
  daily: OverviewResponse["daily"];
}) {
  // Compute max so we can scale heights. Layout: each bar = (date label
  // below, stacked rect above). SVG is responsive via viewBox so it
  // scales to the parent card width.
  const max = Math.max(
    1,
    ...daily.map(
      (d) => d.threads + d.scheduled_tasks + d.automations + d.media,
    ),
  );
  const chartH = 200;
  const padTop = 10;
  const padBottom = 30;
  const yTicks = niceTicks(max, 5);
  const ticksMax = yTicks[yTicks.length - 1] || max;
  const barW = 24;
  const gap = 14;
  const width = Math.max(600, daily.length * (barW + gap) + 60);
  const innerH = chartH - padTop - padBottom;

  const yFor = (v: number) => padTop + innerH - (v / ticksMax) * innerH;

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${width} ${chartH}`}
        className="block min-w-full"
        style={{ height: chartH }}
      >
        {/* Y axis ticks + grid lines */}
        {yTicks.map((t) => {
          const y = yFor(t);
          return (
            <g key={t}>
              <line
                x1={40}
                x2={width}
                y1={y}
                y2={y}
                stroke="#eee"
                strokeWidth={1}
              />
              <text
                x={36}
                y={y + 4}
                fontSize={10}
                textAnchor="end"
                fill="#888"
              >
                {fmtAxisNumber(t)}
              </text>
            </g>
          );
        })}

        {/* Bars */}
        {daily.map((d, i) => {
          const x = 50 + i * (barW + gap);
          let yCursor = padTop + innerH;
          const segments: { key: string; v: number }[] = [
            { key: "threads", v: d.threads },
            { key: "scheduled_tasks", v: d.scheduled_tasks },
            { key: "automations", v: d.automations },
            { key: "media", v: d.media },
          ];
          return (
            <g key={d.date}>
              {segments.map((seg) => {
                if (seg.v <= 0) return null;
                const h = (seg.v / ticksMax) * innerH;
                yCursor -= h;
                return (
                  <rect
                    key={seg.key}
                    x={x}
                    y={yCursor}
                    width={barW}
                    height={h}
                    rx={2}
                    fill={CATEGORY_COLORS[seg.key]}
                  >
                    <title>
                      {d.date} - {labelForCategory(seg.key)}:{" "}
                      {fmtNumber(seg.v)}
                    </title>
                  </rect>
                );
              })}
              <text
                x={x + barW / 2}
                y={chartH - 10}
                fontSize={10}
                textAnchor="middle"
                fill="#888"
              >
                {Number(d.date.slice(-2))}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function niceTicks(max: number, n: number): number[] {
  // Round to a clean ceiling and produce n+1 ticks.
  if (max <= 0) return [0];
  const power = Math.pow(10, Math.floor(Math.log10(max)));
  const ratio = max / power;
  let step: number;
  if (ratio < 1.5) step = power / 5;
  else if (ratio < 3) step = power / 2;
  else if (ratio < 6) step = power;
  else step = power * 2;
  const ceil = Math.ceil(max / step) * step;
  const out: number[] = [];
  for (let v = 0; v <= ceil + step * 0.001; v += step) {
    out.push(Math.round(v));
  }
  return out;
}

function TopList({
  title,
  subtitle,
  rows,
}: {
  title: string;
  subtitle: string;
  rows: { id: string; label: string; credits: number }[];
}) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-white p-5">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <h3 className="text-sm font-semibold text-[var(--color-ink-deep)]">
            {title}
          </h3>
          <p className="text-xs text-neutral-500">{subtitle}</p>
        </div>
      </div>
      {rows.length === 0 ? (
        <div className="py-6 text-center text-xs text-neutral-500">
          Sin actividad en el rango seleccionado.
        </div>
      ) : (
        <ul className="flex flex-col gap-2">
          {rows.map((r) => (
            <li
              key={r.id}
              className="flex items-center justify-between text-sm"
            >
              <span className="truncate text-neutral-800">{r.label}</span>
              <span className="ml-3 shrink-0 tabular-nums font-medium text-[var(--color-ink-deep)]">
                {fmtNumber(r.credits)} credits
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Team tab
// ---------------------------------------------------------------------------

type TeamSortKey =
  | "threads"
  | "scheduled_task_runs"
  | "credits"
  | "last_activity";

function TeamTab({ range }: { range: DateRange }) {
  const { getToken } = useAuth();
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<TeamSortKey>("credits");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const q = useQuery({
    queryKey: ["usage", "team", range],
    queryFn: async (): Promise<TeamResponse> => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return usageApi.team(range, token);
    },
  });

  const filtered = useMemo(() => {
    if (!q.data) return [];
    let rows = q.data.rows;
    if (search.trim()) {
      const needle = search.trim().toLowerCase();
      rows = rows.filter((r) =>
        r.display_name.toLowerCase().includes(needle),
      );
    }
    const sorted = [...rows].sort((a, b) => {
      let av: number, bv: number;
      if (sortKey === "last_activity") {
        av = a.last_activity ? new Date(a.last_activity).getTime() : 0;
        bv = b.last_activity ? new Date(b.last_activity).getTime() : 0;
      } else {
        av = (a as unknown as Record<string, number>)[sortKey] ?? 0;
        bv = (b as unknown as Record<string, number>)[sortKey] ?? 0;
      }
      return sortDir === "desc" ? bv - av : av - bv;
    });
    return sorted;
  }, [q.data, search, sortKey, sortDir]);

  if (q.isLoading) return <SkeletonList />;
  if (q.isError)
    return (
      <ErrorState
        message={(q.error as Error)?.message ?? "Error desconocido"}
        onRetry={() => q.refetch()}
      />
    );
  if (!q.data) return null;

  const toggleSort = (k: TeamSortKey) => {
    if (k === sortKey) setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else {
      setSortKey(k);
      setSortDir("desc");
    }
  };
  const arrow = (k: TeamSortKey) =>
    k !== sortKey ? "" : sortDir === "desc" ? " ↓" : " ↑";

  return (
    <div className="flex flex-col gap-4">
      <StatCard
        label="TOTAL USERS"
        value={String(q.data.total_users)}
        unit="active users in workspace"
      />
      <SearchBar value={search} onChange={setSearch} placeholder="Search people" />
      <div className="overflow-x-auto rounded-lg border border-[var(--color-border)] bg-white">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border)] text-left text-[11px] font-medium uppercase tracking-wide text-neutral-500">
              <th className="px-4 py-3">User</th>
              <th
                className="cursor-pointer px-4 py-3"
                onClick={() => toggleSort("threads")}
              >
                Threads{arrow("threads")}
              </th>
              <th
                className="cursor-pointer px-4 py-3"
                onClick={() => toggleSort("scheduled_task_runs")}
              >
                Scheduled tasks runs{arrow("scheduled_task_runs")}
              </th>
              <th
                className="cursor-pointer px-4 py-3"
                onClick={() => toggleSort("credits")}
              >
                Credits used{arrow("credits")}
              </th>
              <th
                className="cursor-pointer px-4 py-3"
                onClick={() => toggleSort("last_activity")}
              >
                Last activity{arrow("last_activity")}
              </th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr
                key={r.app_user_id ?? "system"}
                className="border-b border-[var(--color-border)] last:border-b-0"
              >
                <td className="px-4 py-3 text-neutral-800">{r.display_name}</td>
                <td className="px-4 py-3 text-neutral-700">
                  {r.threads} threads
                </td>
                <td className="px-4 py-3 text-neutral-700">
                  {r.scheduled_task_runs} runs
                </td>
                <td className="px-4 py-3 font-medium tabular-nums text-[var(--color-ink-deep)]">
                  {fmtNumber(r.credits)} credits
                </td>
                <td className="px-4 py-3 text-neutral-500">
                  {r.last_activity
                    ? formatDistanceToNow(new Date(r.last_activity), {
                        addSuffix: true,
                      })
                    : "never"}
                </td>
              </tr>
            ))}
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-sm text-neutral-500">
                  Sin actividad en el rango seleccionado.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Activity tab
// ---------------------------------------------------------------------------

function ActivityTab({ range }: { range: DateRange }) {
  const { getToken } = useAuth();
  const [page, setPage] = useState(1);
  const [kind, setKind] = useState<ActivityKindFilter>("all");

  const q = useQuery({
    queryKey: ["usage", "activity", range, kind, page],
    queryFn: async (): Promise<ActivityResponse> => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return usageApi.activity({ range, kind, page, pageSize: 20 }, token);
    },
  });

  if (q.isLoading) return <SkeletonList />;
  if (q.isError)
    return (
      <ErrorState
        message={(q.error as Error)?.message ?? "Error desconocido"}
        onRetry={() => q.refetch()}
      />
    );
  if (!q.data) return null;

  const totalPages = Math.max(
    1,
    Math.ceil(q.data.total_count / q.data.page_size),
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <KindFilter value={kind} onChange={setKind} />
      </div>
      <div className="overflow-x-auto rounded-lg border border-[var(--color-border)] bg-white">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border)] text-left text-[11px] font-medium uppercase tracking-wide text-neutral-500">
              <th className="px-4 py-3">Activity</th>
              <th className="px-4 py-3">User</th>
              <th className="px-4 py-3">Credits used</th>
              <th className="px-4 py-3">When</th>
            </tr>
          </thead>
          <tbody>
            {q.data.rows.map((r) => (
              <tr
                key={r.id}
                className="border-b border-[var(--color-border)] last:border-b-0"
              >
                <td className="px-4 py-3">
                  <div className="text-neutral-800">
                    {r.parent_name ?? labelForKind(r.kind)}
                  </div>
                  <div className="text-[11px] text-neutral-500">
                    {labelForKind(r.kind)}
                  </div>
                </td>
                <td className="px-4 py-3 text-neutral-700">
                  {r.user_display_name}
                </td>
                <td className="px-4 py-3 font-medium tabular-nums text-[var(--color-ink-deep)]">
                  {fmtNumber(r.credits)} credits
                </td>
                <td className="px-4 py-3 text-neutral-500">
                  {formatDistanceToNow(new Date(r.started_at), {
                    addSuffix: true,
                  })}
                </td>
              </tr>
            ))}
            {q.data.rows.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-sm text-neutral-500">
                  Sin actividad en el rango seleccionado.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between text-sm text-neutral-600">
        <button
          type="button"
          onClick={() => setPage((p) => Math.max(1, p - 1))}
          disabled={page <= 1}
          className="rounded px-3 py-1 disabled:cursor-not-allowed disabled:opacity-40 hover:bg-[var(--color-surface-fog)]"
        >
          ← Prev
        </button>
        <span>
          Page {q.data.page} / {totalPages}
        </span>
        <button
          type="button"
          onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          disabled={page >= totalPages}
          className="rounded px-3 py-1 disabled:cursor-not-allowed disabled:opacity-40 hover:bg-[var(--color-surface-fog)]"
        >
          Next →
        </button>
      </div>
    </div>
  );
}

function labelForKind(k: string): string {
  return (
    {
      slack_thread: "Slack: thread",
      scheduled_task: "Scheduled task",
      automation: "Automation",
      media: "Media generation",
    } as Record<string, string>
  )[k] ?? k;
}

function KindFilter({
  value,
  onChange,
}: {
  value: ActivityKindFilter;
  onChange: (v: ActivityKindFilter) => void;
}) {
  const labels: Record<ActivityKindFilter, string> = {
    all: "All activity",
    slack_thread: "Threads",
    scheduled_task: "Scheduled tasks",
    automation: "Automations",
    media: "Media",
  };
  return (
    <label className="relative inline-flex items-center">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as ActivityKindFilter)}
        className="appearance-none rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 pr-8 text-sm text-[var(--color-ink-deep)] focus:border-[#FF5200] focus:outline-none"
      >
        {(Object.keys(labels) as ActivityKindFilter[]).map((k) => (
          <option key={k} value={k}>
            {labels[k]}
          </option>
        ))}
      </select>
      <ChevronDown
        className="pointer-events-none absolute right-2 size-4 text-neutral-500"
        strokeWidth={1.75}
      />
    </label>
  );
}

// ---------------------------------------------------------------------------
// Scheduled Tasks tab
// ---------------------------------------------------------------------------

function ScheduledTasksTab({ range }: { range: DateRange }) {
  const { getToken } = useAuth();
  const [systemOnly, setSystemOnly] = useState(false);

  const q = useQuery({
    queryKey: ["usage", "scheduled_tasks", range, systemOnly],
    queryFn: async (): Promise<ScheduledTaskResponse> => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return usageApi.scheduledTasks({ range, systemOnly }, token);
    },
  });

  if (q.isLoading) return <SkeletonList />;
  if (q.isError)
    return (
      <ErrorState
        message={(q.error as Error)?.message ?? "Error desconocido"}
        onRetry={() => q.refetch()}
      />
    );
  if (!q.data) return null;

  return (
    <div className="flex flex-col gap-4">
      <label className="inline-flex w-fit items-center gap-2 rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm">
        <input
          type="checkbox"
          checked={systemOnly}
          onChange={(e) => setSystemOnly(e.target.checked)}
          className="accent-[#FF5200]"
        />
        Show system tasks only
      </label>
      <div className="overflow-x-auto rounded-lg border border-[var(--color-border)] bg-white">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border)] text-left text-[11px] font-medium uppercase tracking-wide text-neutral-500">
              <th className="px-4 py-3">Scheduled Task Name</th>
              <th className="px-4 py-3">Total Runs</th>
              <th className="px-4 py-3">Last activity</th>
              <th className="px-4 py-3">Created by</th>
              <th className="px-4 py-3">Total credits used</th>
            </tr>
          </thead>
          <tbody>
            {q.data.rows.map((r) => (
              <tr
                key={r.task_id ?? r.name}
                className="border-b border-[var(--color-border)] last:border-b-0"
              >
                <td className="px-4 py-3 text-neutral-800">{r.name}</td>
                <td className="px-4 py-3 text-neutral-700">{r.total_runs}</td>
                <td className="px-4 py-3 text-neutral-500">
                  {r.last_activity
                    ? formatDistanceToNow(new Date(r.last_activity), {
                        addSuffix: true,
                      })
                    : "never"}
                </td>
                <td className="px-4 py-3 text-neutral-700">
                  {r.created_by_display_name}
                </td>
                <td className="px-4 py-3 font-medium tabular-nums text-[var(--color-ink-deep)]">
                  {fmtNumber(r.total_credits)} credits
                </td>
              </tr>
            ))}
            {q.data.rows.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-sm text-neutral-500">
                  Sin tareas en el rango seleccionado.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Misc
// ---------------------------------------------------------------------------

function SearchBar({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
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
        placeholder={placeholder}
        className="w-full rounded-lg border border-[var(--color-border)] bg-white py-2 pl-9 pr-3 text-sm text-[var(--color-ink-deep)] placeholder:text-neutral-400 focus:border-[#FF5200] focus:outline-none focus:ring-1 focus:ring-[#FF5200]/30"
      />
    </label>
  );
}

function SkeletonOverview() {
  return (
    <div className="flex flex-col gap-4">
      <div className="h-24 animate-pulse rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-fog)]" />
      <div className="h-72 animate-pulse rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-fog)]" />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="h-48 animate-pulse rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-fog)]" />
        <div className="h-48 animate-pulse rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-fog)]" />
      </div>
    </div>
  );
}

function SkeletonList() {
  return (
    <ul className="flex flex-col gap-3">
      {Array.from({ length: 4 }).map((_, i) => (
        <li
          key={i}
          className="h-16 animate-pulse rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-fog)]"
        />
      ))}
    </ul>
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
      <div className="font-medium">No pude cargar Usage.</div>
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

function fmtNumber(n: number): string {
  // Use the user's locale for grouping; matches the reference's "43.919"
  // style for ES locale, or "43,919" for EN. Either reads fine.
  return new Intl.NumberFormat().format(Math.round(n));
}

function fmtAxisNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1000)}K`;
  return String(n);
}
