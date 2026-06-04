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
import { Users, UserPlus, RefreshCw, Trash2 } from "lucide-react";

import { teamApi, type SyncSlackResponse, type TeamMember } from "@/lib/api/team";

const TEAM_QUERY_KEY = ["team", "members"] as const;

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
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"org:admin" | "org:member">("org:member");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [syncResult, setSyncResult] = useState<SyncSlackResponse | null>(null);

  const inviteMut = useMutation({
    mutationFn: async () => {
      const token = await getToken({ template: "backend" });
      if (!token) throw new Error("No token");
      return teamApi.invite({ email: inviteEmail, role: inviteRole }, token);
    },
    onSuccess: () => {
      setInviteEmail("");
      setErrorMsg(null);
      queryClient.invalidateQueries({ queryKey: TEAM_QUERY_KEY });
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

      {isAdmin ? (
        <section className="rounded-lg border border-[var(--color-border)] bg-white p-4">
          <h2 className="text-sm font-medium text-[var(--color-ink-deep)] mb-2 flex items-center gap-1.5">
            <UserPlus className="size-4" strokeWidth={1.75} />
            Invite teammate
          </h2>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (inviteEmail.trim()) inviteMut.mutate();
            }}
            className="flex flex-wrap items-center gap-2"
          >
            <input
              type="email"
              required
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              placeholder="teammate@example.com"
              className="flex-1 min-w-[200px] rounded-md border border-[var(--color-border)] bg-white px-3 py-1.5 text-sm focus:border-[#FF5200] focus:outline-none focus:ring-1 focus:ring-[#FF5200]/30"
            />
            <select
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value as "org:admin" | "org:member")}
              className="rounded-md border border-[var(--color-border)] bg-white px-2 py-1.5 text-sm"
            >
              <option value="org:member">Member</option>
              <option value="org:admin">Admin</option>
            </select>
            <button
              type="submit"
              disabled={inviteMut.isPending || !inviteEmail.trim()}
              className="rounded-md bg-[#FF5200] px-3 py-1.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {inviteMut.isPending ? "Sending..." : "Send invite"}
            </button>
          </form>
        </section>
      ) : null}

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
