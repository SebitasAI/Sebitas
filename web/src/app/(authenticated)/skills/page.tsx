"use client";

// Skills page (slice T-6). Lists every skill the caller can see -- workspace
// skills + their own personal skills -- with Install / Uninstall buttons.
// Upload of new skills still happens via Slack DM (drop a `.md` file) for v1.

import { useMemo, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles, Search, Plus, Minus, Lock } from "lucide-react";

import { PageBody, PageHeader } from "../_components/page-header";
import {
  skillsApi,
  type Skill,
  type SkillListResponse,
} from "@/lib/api/skills";

const SKILLS_QUERY_KEY = ["skills", "all"] as const;

type FilterMode = "all" | "installed" | "mine";

export default function SkillsPage() {
  return (
    <>
      <PageHeader title="Skills" Icon={Sparkles} />
      <PageBody>
        <SkillsBody />
      </PageBody>
    </>
  );
}

function SkillsBody() {
  const { getToken } = useAuth();
  const queryClient = useQueryClient();

  const skillsQuery = useQuery({
    queryKey: SKILLS_QUERY_KEY,
    queryFn: async (): Promise<SkillListResponse> => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return skillsApi.list(token);
    },
  });

  const all = useMemo(
    () => skillsQuery.data?.skills ?? [],
    [skillsQuery.data],
  );
  const installed = useMemo(
    () => all.filter((s) => s.is_installed),
    [all],
  );
  const mine = useMemo(() => all.filter((s) => s.is_mine), [all]);

  const [tab, setTab] = useState<FilterMode>("all");
  const [search, setSearch] = useState("");

  const visible = useMemo(() => {
    const pool =
      tab === "installed" ? installed : tab === "mine" ? mine : all;
    if (!search.trim()) return pool;
    const needle = search.trim().toLowerCase();
    return pool.filter(
      (s) =>
        s.name.toLowerCase().includes(needle) ||
        s.description.toLowerCase().includes(needle),
    );
  }, [tab, search, all, installed, mine]);

  const counts = {
    all: all.length,
    installed: installed.length,
    mine: mine.length,
  } as const;

  // Mutation factory. React's `useXxx` naming rule applies because we call
  // useMutation inside; the name has to start with `use` to satisfy the
  // hooks-of-hooks lint. Same optimistic-update shape for both ops; we
  // branch on `op` for the API call + the predicted post-state.
  function useSkillMutation(op: "install" | "uninstall") {
    return useMutation({
      mutationFn: async (name: string): Promise<Skill> => {
        const token = await getToken({ template: "backend" });
        if (!token) throw new Error("No Clerk session token available.");
        return op === "install"
          ? skillsApi.install(name, token)
          : skillsApi.uninstall(name, token);
      },
      onMutate: async (name) => {
        await queryClient.cancelQueries({ queryKey: SKILLS_QUERY_KEY });
        const prev =
          queryClient.getQueryData<SkillListResponse>(SKILLS_QUERY_KEY);
        if (prev) {
          queryClient.setQueryData<SkillListResponse>(SKILLS_QUERY_KEY, {
            ...prev,
            skills: prev.skills.map((s) =>
              s.name === name
                ? {
                    ...s,
                    is_installed: op === "install",
                    activation_override:
                      op === "install" ? s.activation_override : null,
                    effective_activation:
                      op === "install"
                        ? s.effective_activation
                        : s.activation_default,
                  }
                : s,
            ),
          });
        }
        return { prev };
      },
      onError: (_err, _name, ctx) => {
        if (ctx?.prev) {
          queryClient.setQueryData(SKILLS_QUERY_KEY, ctx.prev);
        }
      },
      onSuccess: (updated) => {
        queryClient.setQueryData<SkillListResponse>(SKILLS_QUERY_KEY, (curr) =>
          curr
            ? {
                ...curr,
                skills: curr.skills.map((s) =>
                  s.id === updated.id ? updated : s,
                ),
              }
            : curr,
        );
      },
    });
  }

  const installMut = useSkillMutation("install");
  const uninstallMut = useSkillMutation("uninstall");

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm text-neutral-500">
        Las skills son archivos markdown que le dan a Misterr contexto / playbooks
        adicionales. Subilas desde el DM con Misterr (mandale un{" "}
        <code className="rounded bg-[var(--color-surface-fog)] px-1 text-[12px]">
          .md
        </code>{" "}
        y decile &ldquo;instalala como skill&rdquo;). Las que ves acá son las
        del workspace + tus skills personales.
      </p>

      <SearchBar value={search} onChange={setSearch} />

      <Tabs value={tab} counts={counts} onChange={setTab} />

      {skillsQuery.isLoading ? (
        <SkeletonGrid />
      ) : skillsQuery.isError ? (
        <ErrorState
          message={(skillsQuery.error as Error)?.message ?? "Error desconocido"}
          onRetry={() => skillsQuery.refetch()}
        />
      ) : visible.length === 0 ? (
        <EmptyState tab={tab} search={search} />
      ) : (
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {visible.map((skill) => (
            <SkillCard
              key={skill.id}
              skill={skill}
              onInstall={() => installMut.mutate(skill.name)}
              onUninstall={() => uninstallMut.mutate(skill.name)}
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
        placeholder="Buscar skills por nombre o descripción"
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
  value: FilterMode;
  counts: { all: number; installed: number; mine: number };
  onChange: (v: FilterMode) => void;
}) {
  const items: { id: FilterMode; label: string; count: number }[] = [
    { id: "all", label: "All", count: counts.all },
    { id: "installed", label: "Installed", count: counts.installed },
    { id: "mine", label: "Mine", count: counts.mine },
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

function SkeletonGrid() {
  return (
    <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <li
          key={i}
          className="h-40 animate-pulse rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-fog)]"
        />
      ))}
    </ul>
  );
}

function EmptyState({ tab, search }: { tab: FilterMode; search: string }) {
  let message: string;
  if (search.trim()) {
    message = `No hay skills que matcheen "${search}".`;
  } else if (tab === "installed") {
    message =
      "Todavía no instalaste ninguna skill. Tocá Install en alguna de la lista (tab All).";
  } else if (tab === "mine") {
    message =
      "Todavía no subiste ninguna skill propia. Mandá un archivo .md a Misterr por DM y decile 'instalala como skill'.";
  } else {
    message =
      "No hay skills en este workspace todavía. Subí la primera mandándole un .md a Misterr por DM.";
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
      <div className="font-medium">No pude cargar las skills.</div>
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

function SkillCard({
  skill,
  onInstall,
  onUninstall,
}: {
  skill: Skill;
  onInstall: () => void;
  onUninstall: () => void;
}) {
  return (
    <li className="flex flex-col gap-3 rounded-lg border border-[var(--color-border)] bg-white p-4">
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-medium text-[var(--color-ink-deep)]">
              {skill.name}
            </span>
            <ScopeBadge scope={skill.scope} />
            {skill.effective_activation === "always_active" ? (
              <ActivationBadge value="always_active" />
            ) : null}
          </div>
          {skill.created_by_user_id ? null : (
            <div className="mt-0.5 text-[11px] text-neutral-400">
              Sin autor (subida CLI o legacy)
            </div>
          )}
        </div>
        {skill.is_installed ? (
          <button
            type="button"
            onClick={onUninstall}
            className="inline-flex items-center gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-surface-fog)] px-2.5 py-1.5 text-xs font-medium text-neutral-700 hover:bg-white hover:text-[var(--color-ink-deep)]"
          >
            <Minus className="size-3.5" strokeWidth={2} />
            Desinstalar
          </button>
        ) : (
          <button
            type="button"
            onClick={onInstall}
            className="inline-flex items-center gap-1 rounded-md bg-[var(--color-ink-deep)] px-2.5 py-1.5 text-xs font-medium text-white hover:bg-black"
          >
            <Plus className="size-3.5" strokeWidth={2.25} />
            Instalar
          </button>
        )}
      </header>

      <p className="line-clamp-3 text-[13px] text-neutral-600">
        {skill.description}
      </p>

      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-neutral-400">
        <div className="flex flex-wrap gap-2">
          {skill.links.slice(0, 3).map((tag) => (
            <span
              key={tag}
              className="rounded-full bg-[var(--color-surface-fog)] px-2 py-0.5 text-[10px] text-neutral-500"
            >
              #{tag}
            </span>
          ))}
        </div>
        <span>
          v{skill.version} · {Math.max(1, Math.round(skill.size_bytes / 1024))}{" "}
          KB
        </span>
      </div>
    </li>
  );
}

function ScopeBadge({ scope }: { scope: "workspace" | "personal" }) {
  if (scope === "personal") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-medium text-violet-700">
        <Lock className="size-3" strokeWidth={2} />
        Personal
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-full bg-neutral-100 px-2 py-0.5 text-[10px] font-medium text-neutral-600">
      Workspace
    </span>
  );
}

function ActivationBadge({ value }: { value: "always_active" | "on_demand" }) {
  const label = value === "always_active" ? "Always on" : "On demand";
  const cls =
    value === "always_active"
      ? "bg-emerald-100 text-emerald-700"
      : "bg-neutral-100 text-neutral-600";
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${cls}`}
    >
      {label}
    </span>
  );
}
