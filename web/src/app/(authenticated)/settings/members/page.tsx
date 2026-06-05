"use client";

// Team / Members settings page (slice T-5). Replaces the old ComingSoon
// placeholder with the real team-management UI: list current org members,
// invite by email, remove members (admin only), sync against Slack roster.
//
// The list shows everyone in the user's active Clerk Organization; for
// members linked to an AppUser, we also display their Slack identifier.
// The "Sync Slack" action computes a diff (members whose Slack record is
// deleted/deactivated) and offers a confirm-then-apply step.

import { useEffect, useState } from "react";
import { useAuth, useOrganization } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Copy,
  RefreshCw,
  Send,
  Share2,
  Trash2,
  Users,
} from "lucide-react";

import {
  teamApi,
  type SlackRosterEntry,
  type SlackRosterResponse,
  type SyncSlackResponse,
  type TeamMember,
} from "@/lib/api/team";

const TEAM_QUERY_KEY = ["team", "members"] as const;
const ROSTER_QUERY_KEY = ["team", "slack-roster"] as const;

// Public sign-up URL we tell teammates to visit. Override via
// NEXT_PUBLIC_APP_URL if/when we use a different host.
const APP_SIGN_UP_URL = `${process.env.NEXT_PUBLIC_APP_URL ?? "https://app.misterr.ai"}/sign-up`;

export default function SettingsMembersPage() {
  const { getToken } = useAuth();
  const { organization, isLoaded: orgLoaded, membership } = useOrganization();
  const queryClient = useQueryClient();
  const isAdmin = membership?.role === "org:admin";

  const membersQuery = useQuery({
    queryKey: TEAM_QUERY_KEY,
    queryFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No Clerk session token available.");
      return teamApi.listMembers(token);
    },
    enabled: orgLoaded && !!organization,
  });

  // Auto-provision: if the user has no org yet, try to provision via the
  // backend. Common case for users whose workspace pre-dates the org rollout.
  const [provisioning, setProvisioning] = useState(false);
  useEffect(() => {
    if (!orgLoaded) return;
    if (organization) return;
    setProvisioning(true);
    (async () => {
      try {
        const token = await getToken({ template: "backend" });
        if (!token) return;
        await teamApi.provision(token);
        // After provisioning, Clerk needs a session refresh to pick up
        // the new org membership. Easiest: hard reload so Clerk re-fetches.
        window.location.reload();
      } catch (e) {
        console.error("[team] provision failed", e);
      } finally {
        setProvisioning(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgLoaded, organization]);

  return (
    <>
      <PageHeader title="Team" />
      <PageBody>
        {!orgLoaded ? (
          <Skeleton />
        ) : !organization ? (
          <NoOrgState provisioning={provisioning} />
        ) : (
          <TeamView
            membersQuery={membersQuery}
            isAdmin={!!isAdmin}
            queryClient={queryClient}
            getToken={getToken}
          />
        )}
      </PageBody>
    </>
  );
}

function PageHeader({ title }: { title: string }) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-border)] bg-white px-6 py-4 md:px-12">
      <div className="flex items-center gap-2.5">
        <span
          aria-hidden
          className="inline-flex size-6 items-center justify-center rounded-md bg-[#FFE5D6] text-[#FF6A1A]"
        >
          <Users className="size-4" />
        </span>
        <h1 className="text-base font-semibold text-[var(--color-ink-deep)]">{title}</h1>
      </div>
    </header>
  );
}

function PageBody({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col px-6 py-5 md:px-12">
      {children}
    </div>
  );
}

function Skeleton() {
  return (
    <ul className="flex flex-col gap-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <li
          key={i}
          className="h-16 animate-pulse rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-fog)]"
        />
      ))}
    </ul>
  );
}

function NoOrgState({ provisioning }: { provisioning: boolean }) {
  return (
    <div className="rounded-lg border border-dashed border-[var(--color-border)] bg-white py-10 text-center text-sm text-neutral-500">
      {provisioning
        ? "Setting up your organization..."
        : "You don't have an active organization yet. If you just installed Misterr on Slack, wait a few seconds and refresh."}
    </div>
  );
}

function TeamView({
  membersQuery,
  isAdmin,
  queryClient,
  getToken,
}: {
  membersQuery: ReturnType<typeof useQuery>;
  isAdmin: boolean;
  queryClient: ReturnType<typeof useQueryClient>;
  getToken: ReturnType<typeof useAuth>["getToken"];
}) {
  const data = membersQuery.data as { members: TeamMember[]; total: number } | undefined;
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [syncResult, setSyncResult] = useState<SyncSlackResponse | null>(null);

  // Slack roster: list of Slack workspace members, used by the
  // invite-via-DM panel.
  const rosterQuery = useQuery<SlackRosterResponse>({
    queryKey: ROSTER_QUERY_KEY,
    queryFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No token");
      return teamApi.slackRoster(token);
    },
  });

  const dmInviteMut = useMutation({
    mutationFn: async (slackUserId: string) => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No token");
      return teamApi.inviteViaSlackDm(slackUserId, token);
    },
    onSuccess: () => {
      setErrorMsg(null);
    },
    onError: (e: Error) => setErrorMsg(e.message),
  });

  const removeMut = useMutation({
    mutationFn: async (clerkUserId: string) => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No token");
      return teamApi.remove(clerkUserId, token);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: TEAM_QUERY_KEY });
    },
    onError: (e: Error) => setErrorMsg(e.message),
  });

  const syncPreviewMut = useMutation({
    mutationFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No token");
      return teamApi.syncSlack("preview", token);
    },
    onSuccess: (data) => setSyncResult(data),
    onError: (e: Error) => setErrorMsg(e.message),
  });

  const syncApplyMut = useMutation({
    mutationFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No token");
      return teamApi.syncSlack("apply", token);
    },
    onSuccess: (data) => {
      setSyncResult(data);
      queryClient.invalidateQueries({ queryKey: TEAM_QUERY_KEY });
    },
    onError: (e: Error) => setErrorMsg(e.message),
  });

  return (
    <div className="flex flex-col gap-6">
      {membersQuery.isLoading ? (
        <Skeleton />
      ) : (
        <section className="rounded-lg border border-[var(--color-border)] bg-white">
          <header className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)]">
            <h2 className="text-sm font-medium text-[var(--color-ink-deep)]">
              Members ({data?.total ?? 0})
            </h2>
            {isAdmin ? (
              <button
                type="button"
                onClick={() => syncPreviewMut.mutate()}
                disabled={syncPreviewMut.isPending}
                className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-white px-2.5 py-1 text-xs text-neutral-700 hover:bg-[var(--color-surface-fog)] disabled:opacity-50"
              >
                <RefreshCw className="size-3.5" strokeWidth={1.75} />
                Check Slack members
              </button>
            ) : null}
          </header>
          <ul className="divide-y divide-[var(--color-border)]">
            {data?.members?.map((m) => (
              <li key={m.clerk_user_id} className="flex items-center gap-3 px-4 py-3">
                <Avatar member={m} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-sm font-medium text-[var(--color-ink-deep)]">
                    <span className="truncate">{m.name || m.email || m.clerk_user_id}</span>
                    {m.role === "org:admin" ? (
                      <span className="inline-flex items-center rounded-full bg-[#FF5200]/10 px-2 py-0.5 text-[10px] font-medium text-[#FF5200]">
                        Admin
                      </span>
                    ) : null}
                    {!m.slack_user_id ? (
                      <span className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700">
                        Web only
                      </span>
                    ) : null}
                  </div>
                  <div className="text-xs text-neutral-500 truncate">
                    {m.email ?? "—"}
                    {m.slack_user_id ? (
                      <span className="ml-1.5 text-neutral-400">· {m.slack_user_id}</span>
                    ) : null}
                  </div>
                </div>
                {isAdmin ? (
                  <button
                    type="button"
                    onClick={() => removeMut.mutate(m.clerk_user_id)}
                    disabled={removeMut.isPending}
                    className="inline-flex size-7 items-center justify-center rounded-md text-neutral-500 hover:bg-red-50 hover:text-red-600 disabled:opacity-50"
                    title="Remove from team"
                    aria-label="Remove from team"
                  >
                    <Trash2 className="size-3.5" strokeWidth={1.75} />
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      )}

      <ShareLinkPanel url={APP_SIGN_UP_URL} />

      <SlackRosterPanel
        rosterQuery={rosterQuery}
        dmInviteMut={dmInviteMut}
      />

      {syncResult ? (
        <section className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm">
          {syncResult.mode === "preview" && syncResult.to_remove.length === 0 ? (
            <div className="text-amber-900">
              Slack roster and team membership are in sync. Nothing to remove.
            </div>
          ) : syncResult.mode === "preview" ? (
            <>
              <div className="text-amber-900 mb-2 font-medium">
                {syncResult.to_remove.length} member(s) flagged by Slack as removed/deactivated:
              </div>
              <ul className="text-amber-900 mb-3 list-disc pl-5">
                {syncResult.to_remove.map((e) => (
                  <li key={e.clerk_user_id}>
                    {e.email ?? e.clerk_user_id} ({e.reason})
                  </li>
                ))}
              </ul>
              <button
                type="button"
                onClick={() => syncApplyMut.mutate()}
                disabled={syncApplyMut.isPending}
                className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
              >
                Remove these {syncResult.to_remove.length} member(s)
              </button>
              <button
                type="button"
                onClick={() => setSyncResult(null)}
                className="ml-2 rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-xs text-neutral-700 hover:bg-[var(--color-surface-fog)]"
              >
                Cancel
              </button>
            </>
          ) : (
            <div className="text-amber-900">Removed {syncResult.removed.length} member(s).</div>
          )}
        </section>
      ) : null}

      {errorMsg ? (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-700">
          {errorMsg}
        </div>
      ) : null}
    </div>
  );
}

function ShareLinkPanel({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);

  async function copyToClipboard() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // clipboard unavailable (older browsers / iframe sandbox). The
      // URL is still selectable manually in the input below.
    }
  }

  return (
    <section className="rounded-lg border border-[var(--color-border)] bg-white p-4">
      <h2 className="mb-1 flex items-center gap-1.5 text-sm font-medium text-[var(--color-ink-deep)]">
        <Share2 className="size-4" strokeWidth={1.75} />
        Share Misterr with your team
      </h2>
      <p className="mb-3 text-xs text-neutral-500">
        Anyone in your Slack workspace can sign in. Share this link, they
        click &quot;Continue with Slack&quot;, and they appear here automatically.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="text"
          value={url}
          readOnly
          onFocus={(e) => e.currentTarget.select()}
          className="flex-1 min-w-[240px] rounded-md border border-[var(--color-border)] bg-[var(--color-surface-fog)] px-3 py-1.5 text-sm font-mono text-[var(--color-ink-deep)] focus:outline-none focus:ring-1 focus:ring-[#FF5200]/30"
        />
        <button
          type="button"
          onClick={copyToClipboard}
          className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-ink-deep)] px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
        >
          {copied ? (
            <>
              <Check className="size-3.5" strokeWidth={2} />
              Copied
            </>
          ) : (
            <>
              <Copy className="size-3.5" strokeWidth={1.75} />
              Copy
            </>
          )}
        </button>
      </div>
    </section>
  );
}


function SlackRosterPanel({
  rosterQuery,
  dmInviteMut,
}: {
  rosterQuery: ReturnType<typeof useQuery<SlackRosterResponse>>;
  dmInviteMut: ReturnType<typeof useMutation<unknown, Error, string>>;
}) {
  const [filter, setFilter] = useState("");
  const [sentTo, setSentTo] = useState<Set<string>>(new Set());

  const data = rosterQuery.data;
  const filtered = (data?.entries ?? [])
    .filter((e) => !e.is_bot)
    .filter((e) => {
      if (!filter.trim()) return true;
      const needle = filter.trim().toLowerCase();
      return (
        (e.display_name ?? "").toLowerCase().includes(needle) ||
        (e.real_name ?? "").toLowerCase().includes(needle) ||
        (e.email ?? "").toLowerCase().includes(needle)
      );
    });

  // People who haven't signed in to the web app yet -- these are the
  // natural targets for an invite DM. Already-app-users are shown
  // with a "Signed in" badge so the operator knows.
  const notSignedIn = filtered.filter((e) => !e.is_app_user);
  const alreadyIn = filtered.filter((e) => e.is_app_user);

  function send(slackUserId: string) {
    dmInviteMut.mutate(slackUserId, {
      onSuccess: () => setSentTo((prev) => new Set(prev).add(slackUserId)),
    });
  }

  return (
    <section className="rounded-lg border border-[var(--color-border)] bg-white p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="flex items-center gap-1.5 text-sm font-medium text-[var(--color-ink-deep)]">
            <Send className="size-4" strokeWidth={1.75} />
            Invite via Slack DM
          </h2>
          <p className="mt-0.5 text-xs text-neutral-500">
            The Misterr bot will DM each person their sign-up link directly.
          </p>
        </div>
        <input
          type="search"
          placeholder="Search by name or email"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="min-w-[200px] rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-xs focus:border-[#FF5200] focus:outline-none focus:ring-1 focus:ring-[#FF5200]/30"
        />
      </div>

      {rosterQuery.isLoading ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <div
              key={i}
              className="h-12 animate-pulse rounded-md border border-[var(--color-border)] bg-[var(--color-surface-fog)]"
            />
          ))}
        </div>
      ) : data == null || data.total === 0 ? (
        <div className="rounded-md border border-dashed border-[var(--color-border)] py-6 text-center text-xs text-neutral-500">
          No Slack members synced yet. Click &quot;Check Slack members&quot; above
          to fetch the roster from Slack.
        </div>
      ) : notSignedIn.length === 0 && alreadyIn.length === 0 ? (
        <div className="rounded-md border border-dashed border-[var(--color-border)] py-6 text-center text-xs text-neutral-500">
          No matches for &quot;{filter}&quot;.
        </div>
      ) : (
        <ul className="divide-y divide-[var(--color-border)] rounded-md border border-[var(--color-border)]">
          {notSignedIn.map((e) => {
            const sent = sentTo.has(e.slack_user_id);
            const pending =
              dmInviteMut.isPending &&
              (dmInviteMut.variables as string) === e.slack_user_id;
            return (
              <li
                key={e.slack_user_id}
                className="flex items-center gap-3 px-3 py-2"
              >
                <RosterAvatar entry={e} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-[var(--color-ink-deep)]">
                    {e.display_name || e.real_name || e.slack_user_id}
                  </div>
                  <div className="truncate text-xs text-neutral-500">
                    {e.email ?? "—"}
                  </div>
                </div>
                {sent ? (
                  <span className="inline-flex items-center gap-1 rounded-md bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
                    <Check className="size-3" strokeWidth={2} />
                    DM sent
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => send(e.slack_user_id)}
                    disabled={pending}
                    className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-white px-2.5 py-1 text-xs text-[var(--color-ink-deep)] hover:bg-[var(--color-surface-fog)] disabled:opacity-50"
                  >
                    <Send className="size-3" strokeWidth={1.75} />
                    {pending ? "Sending…" : "Send invite"}
                  </button>
                )}
              </li>
            );
          })}
          {alreadyIn.map((e) => (
            <li
              key={e.slack_user_id}
              className="flex items-center gap-3 px-3 py-2 opacity-70"
            >
              <RosterAvatar entry={e} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm text-[var(--color-ink-deep)]">
                  {e.display_name || e.real_name || e.slack_user_id}
                </div>
                <div className="truncate text-xs text-neutral-500">
                  {e.email ?? "—"}
                </div>
              </div>
              <span className="inline-flex items-center gap-1 rounded-md bg-neutral-100 px-2 py-0.5 text-[11px] font-medium text-neutral-600">
                Already signed in
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}


function RosterAvatar({ entry }: { entry: SlackRosterEntry }) {
  const initial =
    (entry.display_name || entry.real_name || entry.email || "?")
      .trim()
      .charAt(0)
      .toUpperCase() || "?";
  return (
    <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-md bg-[#FF5200]/15 text-xs font-bold text-[#FF5200]">
      {initial}
    </span>
  );
}


function Avatar({ member }: { member: TeamMember }) {
  const fallback =
    (member.name || member.email || "?")
      .split(/\s+/)
      .map((w) => w[0])
      .slice(0, 2)
      .join("")
      .toUpperCase() || "?";
  if (member.image_url) {
    // eslint-disable-next-line @next/next/no-img-element
    return (
      <img
        src={member.image_url}
        alt={member.name || member.email || ""}
        className="size-8 shrink-0 rounded-md object-cover"
      />
    );
  }
  return (
    <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-md bg-[#FF5200]/15 text-xs font-bold text-[#FF5200]">
      {fallback}
    </span>
  );
}
