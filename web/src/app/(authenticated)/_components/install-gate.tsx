"use client";

// Forced-install gate. After Clerk sign-up (or after creating a NEW
// Clerk Organization via the org switcher), the user lands inside the
// authenticated layout but the current org may not have a Slack
// workspace where Misterr is installed. Without an installation there
// is nothing for the app to do (no agent to talk to, no usage, no
// billing state). This modal covers the dashboard with an unmissable
// "Install Misterr in Slack" prompt until the current org is linked
// to a Slack install.
//
// Important: the gate decides per CURRENT CLERK ORG, not per user.
// The earlier version only counted workspaces globally and missed
// the "user already has one Slack workspace + just created a fresh
// Clerk org" case -- the gate stayed closed because the OTHER org
// still had Slack installed, even though the active org didn't.
//
// Lifecycle:
//   1. Mount: read `useOrganization()` -> active Clerk org id, then
//      call `/api/workspaces`. Match the workspace whose
//      `clerkOrgId` equals the active org id.
//   2. If no match -> show the install modal. If the user is in
//      Clerk's "Personal account" (no active org), defer to the
//      legacy "any workspace counts" behavior so existing users
//      who haven't migrated to per-org workspaces still see the
//      gate close.
//   3. While the modal is open, poll `/api/workspaces` every 5s so
//      it auto-dismisses once the OAuth lands.

import { useEffect, useState } from "react";
import { useOrganization, useUser } from "@clerk/nextjs";

// Direct 302 to slack.com/oauth/v2/authorize -- skips Bolt's intermediate
// "Add to Slack" HTML page so the user lands at Slack's consent screen
// in one click. The backend mints a fresh CSRF state and redirects.
const SLACK_INSTALL_URL =
  process.env.NEXT_PUBLIC_SLACK_INSTALL_URL ??
  "https://sebitas.onrender.com/slack/install/direct";

const POLL_MS = 5000;


export function InstallGate({ children }: { children: React.ReactNode }) {
  const { isLoaded: userLoaded } = useUser();
  const { organization, isLoaded: orgLoaded } = useOrganization();
  const [hasWorkspace, setHasWorkspace] = useState<boolean | null>(null);

  useEffect(() => {
    if (!userLoaded || !orgLoaded) return;
    let cancelled = false;
    const activeOrgId = organization?.id ?? null;

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
        const data = (await res.json()) as {
          workspaces?: { clerkOrgId?: string | null }[];
        };
        const list = Array.isArray(data.workspaces) ? data.workspaces : [];
        let ok: boolean;
        if (activeOrgId) {
          // Current Clerk org has a Slack-installed workspace?
          ok = list.some((w) => w.clerkOrgId === activeOrgId);
        } else {
          // No active org (Personal account). Fall back to the
          // "any workspace counts" check for backward compat.
          ok = list.length > 0;
        }
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
  }, [userLoaded, orgLoaded, organization?.id, hasWorkspace]);

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
