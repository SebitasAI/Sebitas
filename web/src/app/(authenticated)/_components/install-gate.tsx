"use client";

// Forced-install gate. Blocks the dashboard until the user has at
// least one Slack workspace where Misterr is installed.
//
// The earlier strict per-Clerk-org check (PR #163) trapped real
// users behind the modal: invited members whose Clerk email differs
// from their Slack email, users sitting in Personal Account who own
// orgs they aren't actively switched into, and users with an
// existing Slack install alongside a freshly-created Clerk org.
// All of them have legitimate workspace access; gating them was
// hostile.
//
// New rule: the gate closes whenever `/api/workspaces` returns ANY
// workspace for the current user (the backend already matches by
// email roster + Clerk Org membership). The "create new Clerk org
// -> show install modal" UX from the strict version is a follow-up
// problem better solved by surfacing an explicit "Install Misterr
// here" CTA in the workspace selector -- NOT by gating the whole
// dashboard on it.
//
// Lifecycle:
//   1. Mount: GET /api/workspaces. Empty list -> open. Non-empty -> close.
//   2. While the modal is open, poll every 5s so an OAuth completion
//      in another tab auto-dismisses the modal.

import { useEffect, useState } from "react";
import { useUser } from "@clerk/nextjs";

// Routes through Bolt's default install page. We tried 302-ing straight
// to slack.com/oauth/v2/authorize and it broke for users signed into
// multiple Slack workspaces (Slack auto-routed to whichever was "most
// recently active", which was sometimes a restricted one). Bolt's page
// is one extra click but it lets Slack render its workspace picker
// reliably. Keep this until we ship a Sign-In-With-Slack picker.
const SLACK_INSTALL_URL =
  process.env.NEXT_PUBLIC_SLACK_INSTALL_URL ??
  "https://sebitas.onrender.com/slack/install";

const POLL_MS = 5000;


export function InstallGate({ children }: { children: React.ReactNode }) {
  const { isLoaded: userLoaded } = useUser();
  const [hasWorkspace, setHasWorkspace] = useState<boolean | null>(null);

  useEffect(() => {
    if (!userLoaded) return;
    let cancelled = false;

    async function check() {
      try {
        const res = await fetch("/api/workspaces", { credentials: "include" });
        if (!res.ok) {
          // Auth or network error. Render the dashboard rather than
          // block on a self-inflicted bug; the inner pages will
          // surface their own errors.
          if (!cancelled) setHasWorkspace(true);
          return;
        }
        const data = (await res.json()) as { workspaces?: unknown[] };
        // Backend has already done the matching (email roster + Clerk
        // Org membership). If anything came back, the user has
        // workspace access -- gate closes.
        const ok = Array.isArray(data.workspaces) && data.workspaces.length > 0;
        if (!cancelled) setHasWorkspace(ok);
      } catch {
        if (!cancelled) setHasWorkspace(true);
      }
    }

    void check();
    // Poll while we believe the gate is open so the user doesn't have
    // to manually refresh after Slack OAuth completes.
    const interval = setInterval(() => {
      if (hasWorkspace === false) void check();
    }, POLL_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
    // We intentionally re-run when the active org changes so switching
    // orgs in the header dropdown re-evaluates the gate immediately.
  }, [userLoaded, hasWorkspace]);

  // While the first check is in flight, render children so the page
  // doesn't flicker for users that already have a workspace. The modal
  // only mounts after we've explicitly verified `workspaces.length === 0`.
  if (hasWorkspace === false) {
    return (
      <>
        {children}
        <InstallModal slackInstallUrl={SLACK_INSTALL_URL} />
      </>
    );
  }
  return <>{children}</>;
}


function InstallModal({ slackInstallUrl }: { slackInstallUrl: string }) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="install-gate-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
    >
      <div className="w-full max-w-[560px] rounded-2xl border border-[var(--color-border)] bg-white p-8 shadow-2xl">
        <div className="flex flex-col items-center text-center">
          <div className="mb-5 flex size-14 items-center justify-center rounded-full bg-orange-100">
            <SlackIcon className="size-7" />
          </div>
          <h1
            id="install-gate-title"
            className="text-2xl font-semibold text-[var(--color-ink-deep)]"
          >
            Connect Misterr to your Slack
          </h1>
          <p className="mt-3 text-sm leading-relaxed text-neutral-600">
            Misterr lives inside your Slack workspace. Before continuing,
            install the app in your team&apos;s workspace. It takes about 30 seconds
            (authorize, come back here, done).
          </p>

          <a
            href={slackInstallUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-6 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-[var(--color-ink-deep)] px-4 py-3 text-sm font-semibold text-white hover:opacity-90"
          >
            <SlackIcon className="size-5" />
            Install Misterr on Slack
          </a>

          <p className="mt-4 text-[11px] text-neutral-500">
            Waiting for your installation. This page unlocks automatically
            as soon as Slack confirms.
          </p>
        </div>
      </div>
    </div>
  );
}


function SlackIcon({ className = "size-5" }: { className?: string }) {
  // Slack mark, 4-color squares. Inlined so the modal renders even
  // before icon fonts / external assets load.
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      <path
        d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52ZM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313Z"
        fill="#E01E5A"
      />
      <path
        d="M8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834ZM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312Z"
        fill="#36C5F0"
      />
      <path
        d="M18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834ZM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312Z"
        fill="#2EB67D"
      />
      <path
        d="M15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52ZM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313Z"
        fill="#ECB22E"
      />
    </svg>
  );
}
