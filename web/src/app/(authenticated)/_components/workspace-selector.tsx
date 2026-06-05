"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useUser } from "@clerk/nextjs";
import { Check, ChevronsUpDown, Plus } from "lucide-react";

// Workspace switcher rendered at the top of the sidebar, under the logo.
// Shows the user's current Slack workspace + a dropdown with all
// workspaces where Misterr is installed AND the current user is a member.
//
// Data source (TODO — backend endpoint not built yet):
// - `/api/workspaces` on this Next app should call the Sebitas backend
//   with the user's Clerk identity and return [{ id, name, slackTeamId,
//   iconUrl, primaryEmail }, ...]
// - Until that endpoint exists this component falls back to a single
//   "workspace" derived from the Clerk user (their primary email +
//   inferred display name) so the UI renders without a 4xx.
//
// "Add workspace" sends the user through Slack OAuth to install
// Misterr in another team. The install URL is the backend's
// /slack/install/direct endpoint, which 302s straight to slack.com
// without rendering Bolt's intermediate "Add to Slack" page.

type Workspace = {
  id: string;
  name: string;
  iconUrl: string | null;
  primaryEmail: string | null;
};

const SLACK_INSTALL_URL =
  process.env.NEXT_PUBLIC_SLACK_INSTALL_URL ??
  "https://sebitas.onrender.com/slack/install/direct";

export function WorkspaceSelector() {
  const { user } = useUser();
  const [open, setOpen] = useState(false);
  const [workspaces, setWorkspaces] = useState<Workspace[] | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Outside-click + Escape to close.
  useEffect(() => {
    if (!open) return;
    function onPointer(e: PointerEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Fetch workspaces from backend. The endpoint doesn't exist yet; the
  // catch falls back to a Clerk-derived single workspace so the UI
  // renders. When the backend route lands, this code stays the same;
  // only the fetch will start returning real data.
  useEffect(() => {
    let cancelled = false;
    async function load() {
      let real: Workspace[] = [];
      try {
        const res = await fetch("/api/workspaces", { credentials: "include" });
        if (res.ok) {
          const data = (await res.json()) as { workspaces: Workspace[] };
          real = data.workspaces ?? [];
        }
      } catch {
        // network / parse error — fall through to fallback below.
      }
      if (cancelled) return;
      if (real.length > 0) {
        setWorkspaces(real);
        setActiveId(real[0].id);
        return;
      }
      // Backend returned no workspaces (email not in any SlackUser roster,
      // or env vars not configured, or backend down). Show a single
      // Clerk-derived placeholder so the UI doesn't render with empty
      // state. The user can still navigate the app; the selector just
      // doesn't have real Slack workspace data yet.
      if (!user) return;
      const email = user.primaryEmailAddress?.emailAddress ?? null;
      const fallback: Workspace = {
        id: "clerk-fallback",
        name: deriveWorkspaceName(email, user.firstName),
        iconUrl: user.imageUrl ?? null,
        primaryEmail: email,
      };
      setWorkspaces([fallback]);
      setActiveId(fallback.id);
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [user]);

  const active = workspaces?.find((w) => w.id === activeId) ?? null;
  const name = active?.name ?? "…";

  return (
    <div ref={rootRef} className="relative w-full">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        aria-haspopup="menu"
        aria-expanded={open}
        className="group flex w-full items-center gap-2.5 rounded-lg border border-[var(--color-border)] bg-white p-2 text-left transition-colors hover:bg-[var(--color-surface-fog)]"
      >
        <WorkspaceIcon iconUrl={active?.iconUrl ?? null} fallback={name} />
        <span className="flex-1 truncate text-sm font-medium text-[var(--color-ink-deep)]">
          {name}
        </span>
        <ChevronsUpDown
          className="size-4 shrink-0 text-neutral-400"
          strokeWidth={2}
        />
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute left-0 top-full z-[100] mt-1 w-[260px] overflow-hidden rounded-xl border border-[var(--color-border)] bg-white text-[var(--color-ink-deep)] shadow-[0_16px_40px_rgba(0,0,0,0.18)]"
        >
          <ul className="px-2 py-2">
            {(workspaces ?? []).map((w) => {
              const isActive = w.id === activeId;
              return (
                <li key={w.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setActiveId(w.id);
                      setOpen(false);
                    }}
                    className={[
                      "flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left transition-colors",
                      isActive
                        ? "bg-[var(--color-surface-fog)]"
                        : "hover:bg-[var(--color-surface-fog)]",
                    ].join(" ")}
                  >
                    <WorkspaceIcon iconUrl={w.iconUrl} fallback={w.name} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">
                        {w.name}
                      </div>
                      {w.primaryEmail ? (
                        <div className="truncate text-[11px] text-neutral-500">
                          {w.primaryEmail}
                        </div>
                      ) : null}
                    </div>
                    {isActive ? (
                      <Check
                        className="size-4 shrink-0 text-[#FF5200]"
                        strokeWidth={2.5}
                      />
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="border-t border-[var(--color-border)]" />
          <Link
            href={SLACK_INSTALL_URL}
            target="_self"
            rel="noopener"
            className="flex items-center gap-2.5 px-4 py-2.5 text-sm transition-colors hover:bg-[var(--color-surface-fog)]"
          >
            <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-lg border border-dashed border-[var(--color-border)] text-neutral-400">
              <Plus className="size-4" strokeWidth={2} />
            </span>
            <span className="text-sm font-medium">Add workspace</span>
          </Link>
        </div>
      ) : null}
    </div>
  );
}

function WorkspaceIcon({
  iconUrl,
  fallback,
}: {
  iconUrl: string | null;
  fallback: string;
}) {
  const initial = fallback.trim().slice(0, 1).toUpperCase() || "?";
  if (iconUrl) {
    // eslint-disable-next-line @next/next/no-img-element
    return (
      <img
        src={iconUrl}
        alt={fallback}
        className="size-7 shrink-0 rounded-lg object-cover"
      />
    );
  }
  return (
    <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-lg bg-[#FF5200]/15 text-[11px] font-bold text-[#FF5200]">
      {initial}
    </span>
  );
}

function deriveWorkspaceName(
  email: string | null,
  firstName: string | null,
): string {
  if (firstName) return `${firstName}'s workspace`;
  if (email) {
    const domain = email.split("@")[1]?.split(".")[0];
    if (domain) return domain.charAt(0).toUpperCase() + domain.slice(1);
  }
  return "My workspace";
}
