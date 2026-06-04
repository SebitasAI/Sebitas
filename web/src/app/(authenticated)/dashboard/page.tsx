"use client";

// Dashboard / Home. Three-section layout inspired by Viktor's product
// dashboard:
//
//   1. Three KPI cards (credits / scheduled tasks / connected
//      integrations), each with a deep-link to the relevant subpage.
//   2. "Setup" onboarding checklist that disappears automatically
//      once every step is done.
//   3. Recent activity feed (last 10 agent runs across the workspace).
//      Replaces the "What's new" panel since we don't ship product news
//      yet; activity data is already in `agent_run` and answers the
//      natural question "what's happening here?".
//
// All four data sources are existing REST endpoints we already use
// elsewhere (`/api/billing/overview`, `/api/scheduled-tasks`,
// `/api/integrations/connections`, `/api/usage/activity`). The page
// fetches them in parallel via react-query so the slowest call sets
// the perceived load time.

import Link from "next/link";
import { useAuth, useUser } from "@clerk/nextjs";
import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import {
  Activity,
  ArrowRight,
  Check,
  CreditCard,
  Plug,
  Sparkles,
  Timer,
} from "lucide-react";

import { HomeIcon } from "../_components/nav-icons";
import { PageBody, PageHeader } from "../_components/page-header";
import { billingApi, type BillingOverview } from "@/lib/api/billing";
import { integrationsApi, type ConnectionsResponse } from "@/lib/api/integrations";
import { scheduledTasksApi, type TaskListResponse } from "@/lib/api/scheduled-tasks";
import { usageApi, type ActivityResponse } from "@/lib/api/usage";

const NUMBER_FORMAT = new Intl.NumberFormat("en-US");
const fmt = (n: number) => NUMBER_FORMAT.format(Math.round(n));


export default function DashboardPage() {
  const { getToken } = useAuth();
  const { user } = useUser();
  const firstName = user?.firstName ?? "there";

  const billingQuery = useQuery<BillingOverview>({
    queryKey: ["dashboard", "billing"],
    queryFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk token");
      return billingApi.overview(token);
    },
  });

  const tasksQuery = useQuery<TaskListResponse>({
    queryKey: ["dashboard", "tasks"],
    queryFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk token");
      return scheduledTasksApi.list("all", token);
    },
  });

  const integrationsQuery = useQuery<ConnectionsResponse>({
    queryKey: ["dashboard", "integrations"],
    queryFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk token");
      return integrationsApi.listConnections(null, token);
    },
  });

  const activityQuery = useQuery<ActivityResponse>({
    queryKey: ["dashboard", "activity"],
    queryFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk token");
      return usageApi.activity(
        { kind: "all", range: "30d", page: 1, pageSize: 10 },
        token,
      );
    },
  });

  const credits = billingQuery.data?.balance_credits ?? 0;
  const isUnlimited = billingQuery.data?.is_unlimited ?? false;
  const scheduledCount = tasksQuery.data?.total_count ?? 0;
  const integrationsCount = integrationsQuery.data?.connections.length ?? 0;
  const activityRows = activityQuery.data?.rows ?? [];

  return (
    <>
      <PageHeader title="Home" Icon={HomeIcon} />
      <PageBody>
        <h1 className="text-xl font-semibold tracking-tight text-[var(--color-ink-deep)]">
          Hi, {firstName} <span aria-hidden>👋</span>
        </h1>

        <div className="mt-5 grid gap-3 sm:grid-cols-3">
          <KpiCard
            label="Credits available"
            value={isUnlimited ? "Unlimited" : fmt(credits)}
            href="/settings/billing"
            ctaLabel="Manage plan"
            Icon={CreditCard}
            loading={billingQuery.isLoading}
          />
          <KpiCard
            label="Scheduled tasks"
            value={fmt(scheduledCount)}
            href="/scheduled-tasks"
            ctaLabel="Manage tasks"
            Icon={Timer}
            loading={tasksQuery.isLoading}
          />
          <KpiCard
            label="Connected integrations"
            value={fmt(integrationsCount)}
            href="/integrations"
            ctaLabel="Browse integrations"
            Icon={Plug}
            loading={integrationsQuery.isLoading}
          />
        </div>

        <OnboardingChecklist
          integrationsCount={integrationsCount}
          scheduledCount={scheduledCount}
          activityCount={activityRows.length}
        />

        <ActivityPanel
          rows={activityRows}
          loading={activityQuery.isLoading}
        />
      </PageBody>
    </>
  );
}


function KpiCard({
  label,
  value,
  href,
  ctaLabel,
  Icon,
  loading,
}: {
  label: string;
  value: string;
  href: string;
  ctaLabel: string;
  Icon: React.ComponentType<{ className?: string }>;
  loading: boolean;
}) {
  return (
    <div className="relative rounded-xl border border-[var(--color-border)] bg-white p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-neutral-500">
          {label}
        </p>
        <Icon className="size-4 text-neutral-400" />
      </div>
      <p className="mt-2 text-3xl font-semibold tracking-tight text-[var(--color-ink-deep)]">
        {loading ? (
          <span className="inline-block h-7 w-20 animate-pulse rounded bg-neutral-100" />
        ) : (
          value
        )}
      </p>
      <Link
        href={href}
        className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-neutral-600 hover:text-[var(--color-ink-deep)]"
      >
        {ctaLabel}
        <ArrowRight className="size-3" />
      </Link>
    </div>
  );
}


function OnboardingChecklist({
  integrationsCount,
  scheduledCount,
  activityCount,
}: {
  integrationsCount: number;
  scheduledCount: number;
  activityCount: number;
}) {
  // The "Misterr installed in Slack" step is implicit: if the user is
  // here at /dashboard, the InstallGate (in the authenticated layout)
  // already confirmed there's a workspace. We surface it as completed
  // so the checklist starts at 1/4 instead of 0/4 -- gives a sense of
  // progress on first paint.
  const steps = [
    { label: "Install Misterr on your Slack workspace", done: true },
    {
      label: "Connect your first integration",
      done: integrationsCount > 0,
      href: "/integrations",
    },
    {
      label: "Talk to Misterr on Slack (first agent run)",
      done: activityCount > 0,
    },
    {
      label: "Schedule your first task",
      done: scheduledCount > 0,
      href: "/scheduled-tasks",
    },
  ];
  const doneCount = steps.filter((s) => s.done).length;
  const totalCount = steps.length;
  const percent = Math.round((doneCount / totalCount) * 100);

  // Hide once everything is checked off. Don't clutter the home for
  // active customers.
  if (doneCount === totalCount) return null;

  return (
    <div className="mt-6 rounded-xl border border-[var(--color-border)] bg-white p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-base font-semibold text-[var(--color-ink-deep)]">
            Setup
          </h2>
          <p className="mt-1 text-xs text-neutral-500">
            Finish these steps to get Misterr ready in your workspace.
          </p>
        </div>
        <span className="rounded-full bg-orange-50 px-2.5 py-1 text-[11px] font-semibold text-orange-700">
          {doneCount} / {totalCount}
        </span>
      </div>

      <div className="mt-3 h-1.5 rounded-full bg-neutral-100">
        <div
          className="h-1.5 rounded-full bg-[var(--color-ink-deep)] transition-all"
          style={{ width: `${percent}%` }}
        />
      </div>

      <ul className="mt-5 flex flex-col gap-2.5">
        {steps.map((s) => (
          <li
            key={s.label}
            className="flex items-center justify-between gap-3"
          >
            <div className="flex items-center gap-2.5">
              <span
                className={`flex size-5 shrink-0 items-center justify-center rounded-full border ${
                  s.done
                    ? "border-emerald-600 bg-emerald-600 text-white"
                    : "border-[var(--color-border)] bg-white text-transparent"
                }`}
              >
                <Check className="size-3" strokeWidth={3} />
              </span>
              <span
                className={`text-xs ${
                  s.done
                    ? "text-neutral-500 line-through"
                    : "text-[var(--color-ink-deep)]"
                }`}
              >
                {s.label}
              </span>
            </div>
            {!s.done && s.href && (
              <Link
                href={s.href}
                className="inline-flex shrink-0 items-center gap-1 rounded-md border border-[var(--color-border)] bg-white px-2.5 py-1 text-[11px] font-medium text-neutral-700 hover:bg-neutral-50"
              >
                Get started
                <ArrowRight className="size-3" />
              </Link>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}


function ActivityPanel({
  rows,
  loading,
}: {
  rows: ActivityResponse["rows"];
  loading: boolean;
}) {
  return (
    <div className="mt-6 rounded-xl border border-[var(--color-border)] bg-white p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-base font-semibold text-[var(--color-ink-deep)]">
            <Activity className="size-4" />
            Recent activity
          </h2>
          <p className="mt-1 text-xs text-neutral-500">
            Last 10 team conversations with Misterr.
          </p>
        </div>
        <Link
          href="/usage"
          className="inline-flex items-center gap-1 text-xs font-medium text-neutral-600 hover:text-[var(--color-ink-deep)]"
        >
          View all
          <ArrowRight className="size-3" />
        </Link>
      </div>

      <div className="mt-4">
        {loading && (
          <div className="space-y-2">
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                className="h-10 animate-pulse rounded-md bg-neutral-50"
              />
            ))}
          </div>
        )}
        {!loading && rows.length === 0 && (
          <div className="rounded-md border border-dashed border-[var(--color-border)] bg-neutral-50 p-6 text-center">
            <Sparkles className="mx-auto size-5 text-neutral-400" />
            <p className="mt-2 text-xs text-neutral-500">
              When someone talks to Misterr on Slack, it&apos;ll show up here.
            </p>
          </div>
        )}
        {!loading && rows.length > 0 && (
          <ul className="divide-y divide-[var(--color-border)]">
            {rows.map((r) => (
              <ActivityRowItem key={r.id} row={r} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}


function ActivityRowItem({ row }: { row: ActivityResponse["rows"][number] }) {
  const kindLabel: Record<string, string> = {
    slack_thread: "Slack thread",
    scheduled_task: "Scheduled task",
    automation: "Automation",
    media: "Media",
  };
  const label = kindLabel[row.kind] ?? row.kind;
  const subject = row.parent_name ?? label;

  return (
    <li className="flex items-center justify-between gap-3 py-2.5">
      <div className="min-w-0">
        <p className="truncate text-xs font-medium text-[var(--color-ink-deep)]">
          {row.user_display_name}
          <span className="ml-1.5 font-normal text-neutral-500">
            · {subject}
          </span>
        </p>
        <p className="mt-0.5 text-[11px] text-neutral-400">
          {formatDistanceToNow(new Date(row.started_at), { addSuffix: true })}
          {row.credits > 0 && ` · ${fmt(row.credits)} credits`}
          {row.status === "failed" && (
            <span className="ml-1 rounded bg-red-50 px-1 text-red-600">
              error
            </span>
          )}
        </p>
      </div>
    </li>
  );
}
