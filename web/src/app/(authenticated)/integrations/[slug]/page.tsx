"use client";

// Integration detail page (slice T-6). Lists every connected account for
// one app and lets the caller connect another. Mirrors Viktor's per-app
// view: account rows with label, scope chip, owner, and a delete action.

import { useState } from "react";
import { use as usePromise } from "react";
import Link from "next/link";
import { useAuth, useOrganization } from "@clerk/nextjs";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { ChevronLeft, Plus, Trash2, Users as TeamIcon, Lock } from "lucide-react";

import {
  integrationsApi,
  type Connection,
  type CatalogApp,
} from "@/lib/api/integrations";

export default function IntegrationDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = usePromise(params);
  return <Body slug={slug} />;
}

function Body({ slug }: { slug: string }) {
  const { getToken } = useAuth();
  const { membership } = useOrganization();
  const queryClient = useQueryClient();
  const isAdmin = membership?.role === "org:admin";

  const catalogQuery = useQuery({
    queryKey: ["integrations", "catalog", "all"],
    queryFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No token");
      return integrationsApi.listCatalog(false, token);
    },
    staleTime: 60 * 60 * 1000,
  });
  const app: CatalogApp | undefined = catalogQuery.data?.apps.find(
    (a) => a.slug === slug,
  );

  const connectionsQuery = useQuery({
    queryKey: ["integrations", "connections", slug],
    queryFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No token");
      return integrationsApi.listConnections(slug, token);
    },
  });

  const [showConnect, setShowConnect] = useState(false);

  const removeMut = useMutation({
    mutationFn: async (id: string) => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No token");
      return integrationsApi.deleteConnection(id, token);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["integrations", "connections", slug] });
      queryClient.invalidateQueries({ queryKey: ["integrations", "connections", "all"] });
    },
  });

  return (
    <>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-border)] bg-white px-6 py-4 md:px-12">
        <div className="flex items-center gap-3">
          <Link
            href="/integrations"
            className="inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-xs text-neutral-500 transition-colors hover:bg-[var(--color-surface-fog)] hover:text-[var(--color-ink-deep)]"
          >
            <ChevronLeft className="size-3.5" strokeWidth={1.75} />
            Integrations
          </Link>
          <span className="text-neutral-300">/</span>
          {app?.logo_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={app.logo_url} alt="" className="size-7 rounded-md object-contain" />
          ) : null}
          <h1 className="text-base font-semibold text-[var(--color-ink-deep)]">
            {app?.name ?? slug}
          </h1>
        </div>
        <button
          type="button"
          onClick={() => setShowConnect(true)}
          className="inline-flex items-center gap-1.5 rounded-md bg-[#FF5200] px-3 py-1.5 text-sm font-medium text-white hover:bg-[#E64600]"
        >
          <Plus className="size-4" strokeWidth={2} />
          Connect another account
        </button>
      </header>

      <div className="mx-auto flex w-full max-w-5xl flex-col px-6 py-5 md:px-12">
        {app?.description ? (
          <p className="mb-5 text-sm text-neutral-500">{app.description}</p>
        ) : null}

        {connectionsQuery.isLoading ? (
          <ul className="flex flex-col gap-2">
            {Array.from({ length: 2 }).map((_, i) => (
              <li
                key={i}
                className="h-14 animate-pulse rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-fog)]"
              />
            ))}
          </ul>
        ) : (connectionsQuery.data?.connections.length ?? 0) === 0 ? (
          <div className="rounded-lg border border-dashed border-[var(--color-border)] bg-white py-10 text-center text-sm text-neutral-500">
            You don&apos;t have any connected accounts yet. Click &quot;Connect another account&quot;
            to get started.
          </div>
        ) : (
          <ul className="rounded-lg border border-[var(--color-border)] bg-white">
            {connectionsQuery.data!.connections.map((c) => (
              <li
                key={c.id}
                className="flex items-center gap-3 border-b border-[var(--color-border)] px-4 py-3 last:border-b-0"
              >
                <StatusDot status={c.status} />
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-[var(--color-ink-deep)]">
                    {c.account_label || `${app?.name ?? slug} account`}
                  </div>
                  <div className="mt-0.5 flex items-center gap-1.5 text-xs text-neutral-500">
                    <ScopeChip scope={c.scope} />
                    {c.owner_display ? <span>· {c.owner_display}</span> : null}
                    <span>· {c.provider}</span>
                  </div>
                </div>
                {(c.scope === "private") || isAdmin ? (
                  <button
                    type="button"
                    onClick={() => {
                      if (confirm(`Disconnect ${c.account_label || c.app}?`)) {
                        removeMut.mutate(c.id);
                      }
                    }}
                    disabled={removeMut.isPending}
                    className="inline-flex size-7 items-center justify-center rounded-md text-neutral-500 hover:bg-red-50 hover:text-red-600 disabled:opacity-50"
                    title="Disconnect"
                    aria-label="Disconnect"
                  >
                    <Trash2 className="size-3.5" strokeWidth={1.75} />
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>

      {showConnect ? (
        <ConnectModal
          app={app}
          slug={slug}
          isAdmin={!!isAdmin}
          onClose={() => setShowConnect(false)}
          onConnected={() => {
            setShowConnect(false);
            queryClient.invalidateQueries({ queryKey: ["integrations", "connections", slug] });
            queryClient.invalidateQueries({ queryKey: ["integrations", "connections", "all"] });
          }}
        />
      ) : null}
    </>
  );
}

function StatusDot({ status }: { status: string }) {
  const cls =
    status === "connected"
      ? "bg-emerald-500"
      : status === "pending"
        ? "bg-amber-500"
        : "bg-neutral-400";
  return <span className={`inline-block size-2 rounded-full ${cls}`} title={status} />;
}

function ScopeChip({ scope }: { scope: Connection["scope"] }) {
  if (scope === "team") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-neutral-100 px-1.5 py-0.5 text-[10px] font-medium text-neutral-700">
        <TeamIcon className="size-2.5" strokeWidth={2} />
        Team
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
      <Lock className="size-2.5" strokeWidth={2} />
      Private
    </span>
  );
}

function ConnectModal({
  app,
  slug,
  isAdmin,
  onClose,
  onConnected,
}: {
  app: CatalogApp | undefined;
  slug: string;
  isAdmin: boolean;
  onClose: () => void;
  onConnected: () => void;
}) {
  const { getToken } = useAuth();
  const [label, setLabel] = useState("");
  const [scope, setScope] = useState<"team" | "private">("private");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const createMut = useMutation({
    mutationFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No token");
      return integrationsApi.createConnection(
        {
          app: slug,
          scope,
          account_label: label.trim() || null,
          redirect_url: window.location.href,
        },
        token,
      );
    },
    onSuccess: (data) => {
      // Provider-hosted OAuth: redirect the user. They come back via the
      // backend webhook which flips the row to status='connected'.
      window.location.href = data.connect_url;
      onConnected();
    },
    onError: (e: Error) => setErrorMsg(e.message),
  });

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-base font-semibold text-[var(--color-ink-deep)]">
          Connect a new {app?.name ?? slug} account
        </h2>
        <p className="mt-1 text-xs text-neutral-500">
          We&apos;ll redirect you to {app?.name ?? slug} to authorize Misterr.
        </p>

        <label className="mt-4 block text-xs text-neutral-600">
          Nickname for this account (optional)
        </label>
        <input
          type="text"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder={`${app?.name ?? slug} account`}
          maxLength={128}
          className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm focus:border-[#FF5200] focus:outline-none focus:ring-1 focus:ring-[#FF5200]/30"
        />
        <p className="mt-1 text-[10px] text-neutral-400">
          Just a label to tell your accounts apart.
        </p>

        <label className="mt-4 block text-xs text-neutral-600">Who should have access?</label>
        <div className="mt-1 flex flex-col gap-1.5">
          <ScopeRadio
            value="private"
            label="Private (only me)"
            description="Only you can use this account from Slack or from your scheduled tasks."
            checked={scope === "private"}
            onChange={() => setScope("private")}
          />
          <ScopeRadio
            value="team"
            label="Team-wide"
            description={
              isAdmin
                ? "Anyone in the workspace can use it. Only admins can create this option."
                : "Only admins can create team-wide accounts."
            }
            checked={scope === "team"}
            onChange={() => setScope("team")}
            disabled={!isAdmin}
          />
        </div>

        {errorMsg ? (
          <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-700">
            {errorMsg}
          </div>
        ) : null}

        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-xs text-neutral-700 hover:bg-[var(--color-surface-fog)]"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={createMut.isPending}
            onClick={() => createMut.mutate()}
            className="rounded-md bg-[#FF5200] px-3 py-1.5 text-sm font-medium text-white hover:bg-[#E64600] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {createMut.isPending ? "Connecting..." : `Continue to ${app?.name ?? slug}`}
          </button>
        </div>
      </div>
    </div>
  );
}

function ScopeRadio({
  value,
  label,
  description,
  checked,
  onChange,
  disabled,
}: {
  value: string;
  label: string;
  description: string;
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
}) {
  return (
    <label
      className={`flex cursor-pointer items-start gap-2 rounded-md border p-2 ${
        checked
          ? "border-[#FF5200] bg-[#FF5200]/5"
          : "border-[var(--color-border)] hover:bg-[var(--color-surface-fog)]"
      } ${disabled ? "cursor-not-allowed opacity-50" : ""}`}
    >
      <input
        type="radio"
        name="scope"
        value={value}
        checked={checked}
        onChange={onChange}
        disabled={disabled}
        className="mt-0.5 accent-[#FF5200]"
      />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-[var(--color-ink-deep)]">{label}</div>
        <div className="text-[11px] text-neutral-500">{description}</div>
      </div>
    </label>
  );
}
