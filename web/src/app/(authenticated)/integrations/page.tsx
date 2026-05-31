"use client";

// Integrations catalog page (slice T-6). Mirrors Viktor's screenshots:
// grid of cards (one per app), search, All vs Popular tabs, connected-only
// toggle, "N accounts connected" badge per card. Clicking a card navigates
// to /integrations/[slug] for the detail / connect flow.

import { useMemo, useState } from "react";
import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { useQuery } from "@tanstack/react-query";
import { Search, Plug } from "lucide-react";

import { PageBody, PageHeader } from "../_components/page-header";
import {
  integrationsApi,
  type CatalogApp,
  type Connection,
} from "@/lib/api/integrations";

const CATALOG_KEY = ["integrations", "catalog", "all"] as const;
const CONNECTIONS_KEY = ["integrations", "connections", "all"] as const;

type Tab = "all" | "popular";

export default function IntegrationsPage() {
  return (
    <>
      <PageHeader title="Integrations" Icon={Plug} />
      <PageBody>
        <IntegrationsBody />
      </PageBody>
    </>
  );
}

function IntegrationsBody() {
  const { getToken } = useAuth();

  const catalogQuery = useQuery({
    queryKey: CATALOG_KEY,
    queryFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return integrationsApi.listCatalog(false, token);
    },
    staleTime: 60 * 60 * 1000, // catalog is cached server-side too; 1h here matches
  });

  const connectionsQuery = useQuery({
    queryKey: CONNECTIONS_KEY,
    queryFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return integrationsApi.listConnections(null, token);
    },
  });

  const apps = catalogQuery.data?.apps ?? [];
  const connections = connectionsQuery.data?.connections ?? [];

  // Build a lookup: slug -> number of connections (visible to the caller).
  const connCountBySlug = useMemo(() => {
    const m = new Map<string, number>();
    for (const c of connections) {
      m.set(c.app, (m.get(c.app) ?? 0) + 1);
    }
    return m;
  }, [connections]);

  const [tab, setTab] = useState<Tab>("all");
  const [search, setSearch] = useState("");
  const [connectedOnly, setConnectedOnly] = useState(false);

  const visible = useMemo(() => {
    let pool = apps;
    if (tab === "popular") pool = pool.filter((a) => a.popular);
    if (connectedOnly) pool = pool.filter((a) => (connCountBySlug.get(a.slug) ?? 0) > 0);
    const needle = search.trim().toLowerCase();
    if (needle) {
      pool = pool.filter(
        (a) =>
          a.name.toLowerCase().includes(needle) ||
          a.slug.includes(needle) ||
          (a.description ?? "").toLowerCase().includes(needle),
      );
    }
    return pool;
  }, [apps, tab, search, connectedOnly, connCountBySlug]);

  const popularCount = apps.filter((a) => a.popular).length;

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm text-neutral-500">
        Conectá las apps que usás y dejá que Misterr ejecute tareas en
        ellas. Cuando una app está en Composio y Pipedream, usamos Composio.
      </p>

      <SearchBar value={search} onChange={setSearch} />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs tab={tab} setTab={setTab} allCount={apps.length} popularCount={popularCount} />
        <label className="flex items-center gap-2 text-xs text-neutral-600">
          <input
            type="checkbox"
            checked={connectedOnly}
            onChange={(e) => setConnectedOnly(e.target.checked)}
            className="size-3.5"
          />
          Show connected only
        </label>
      </div>

      {catalogQuery.isLoading ? (
        <SkeletonGrid />
      ) : catalogQuery.isError ? (
        <ErrorBox message={(catalogQuery.error as Error)?.message ?? "?"} />
      ) : visible.length === 0 ? (
        <EmptyState search={search} tab={tab} connectedOnly={connectedOnly} />
      ) : (
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((app) => (
            <li key={app.slug}>
              <AppCard app={app} connections={connections.filter((c) => c.app === app.slug)} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SearchBar({ value, onChange }: { value: string; onChange: (v: string) => void }) {
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
        placeholder="Buscar en el catálogo de integraciones"
        className="w-full rounded-lg border border-[var(--color-border)] bg-white py-2 pl-9 pr-3 text-sm text-[var(--color-ink-deep)] placeholder:text-neutral-400 focus:border-[#FF5200] focus:outline-none focus:ring-1 focus:ring-[#FF5200]/30"
      />
    </label>
  );
}

function Tabs({
  tab,
  setTab,
  allCount,
  popularCount,
}: {
  tab: Tab;
  setTab: (t: Tab) => void;
  allCount: number;
  popularCount: number;
}) {
  const items: { id: Tab; label: string; count: number }[] = [
    { id: "all", label: "All integrations", count: allCount },
    { id: "popular", label: "Popular", count: popularCount },
  ];
  return (
    <div role="tablist" className="flex gap-1 rounded-lg bg-[var(--color-surface-fog)] p-1 text-sm">
      {items.map((it) => {
        const active = it.id === tab;
        return (
          <button
            key={it.id}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => setTab(it.id)}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-colors ${
              active
                ? "bg-white text-[var(--color-ink-deep)] shadow-sm"
                : "text-neutral-600 hover:text-[var(--color-ink-deep)]"
            }`}
          >
            <span>{it.label}</span>
            <span
              className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${
                active ? "bg-[#FF5200]/15 text-[#FF5200]" : "bg-white text-neutral-500"
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
    <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 9 }).map((_, i) => (
        <li
          key={i}
          className="h-20 animate-pulse rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-fog)]"
        />
      ))}
    </ul>
  );
}

function EmptyState({
  search,
  tab,
  connectedOnly,
}: {
  search: string;
  tab: Tab;
  connectedOnly: boolean;
}) {
  let msg = "No hay integraciones en el catálogo.";
  if (search.trim()) msg = `Nada matchea "${search}".`;
  else if (connectedOnly) msg = "No tenés cuentas conectadas todavía.";
  else if (tab === "popular") msg = "No hay populares en este momento.";
  return (
    <div className="rounded-lg border border-dashed border-[var(--color-border)] bg-white py-10 text-center text-sm text-neutral-500">
      {msg}
    </div>
  );
}

function ErrorBox({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
      No pude cargar el catálogo: {message}
    </div>
  );
}

function AppCard({ app, connections }: { app: CatalogApp; connections: Connection[] }) {
  const count = connections.length;
  return (
    <Link
      href={`/integrations/${encodeURIComponent(app.slug)}`}
      className="block rounded-lg border border-[var(--color-border)] bg-white p-3 transition-colors hover:border-[#FF5200]/40 hover:shadow-sm"
    >
      <div className="flex items-start gap-3">
        <AppLogo app={app} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-[var(--color-ink-deep)]">
              {app.name}
            </span>
            {count > 0 ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">
                <span className="size-1.5 rounded-full bg-emerald-500" />
                {count} connected
              </span>
            ) : null}
          </div>
          {app.description ? (
            <p className="mt-0.5 line-clamp-2 text-xs text-neutral-500">
              {app.description}
            </p>
          ) : null}
        </div>
      </div>
    </Link>
  );
}

function AppLogo({ app }: { app: CatalogApp }) {
  if (app.logo_url) {
    // eslint-disable-next-line @next/next/no-img-element
    return (
      <img
        src={app.logo_url}
        alt=""
        className="size-9 shrink-0 rounded-md object-contain"
      />
    );
  }
  const initial = (app.name || app.slug).slice(0, 1).toUpperCase();
  return (
    <span className="inline-flex size-9 shrink-0 items-center justify-center rounded-md bg-[#FF5200]/10 text-sm font-bold text-[#FF5200]">
      {initial}
    </span>
  );
}
