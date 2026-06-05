import { NextResponse } from "next/server";
import { auth, clerkClient, currentUser } from "@clerk/nextjs/server";

// Returns the workspaces the current user can access. Matched two ways
// (the backend unions the results):
//
//   1. EMAIL match against the cached Slack user roster -- works for
//      users whose Clerk email is the same as their Slack email.
//
//   2. CLERK ORG match against `workspace.clerk_org_id` -- works for
//      users invited to a workspace via Clerk Organization (their Clerk
//      email may differ from their Slack email, e.g. Alberto signed up
//      as alberto@misterr.ai but is `alberto@antiff.io` in Slack).
//
// Without (2), invited users see the install gate forever even though
// Misterr is already installed in the team they joined.
//
// Required env vars:
// - MISTERR_BACKEND_URL: e.g. https://sebitas.onrender.com
// - MISTERR_WEB_API_KEY: shared secret matching the backend's setting
//
// If either is missing or the backend returns non-200, we return
// { workspaces: [] } so the WorkspaceSelector falls back gracefully
// instead of throwing.

export async function GET() {
  const { userId } = await auth();
  if (!userId) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const user = await currentUser();
  const email = user?.emailAddresses.find(
    (e) => e.id === user?.primaryEmailAddressId,
  )?.emailAddress;

  // List the Clerk orgs the user belongs to. Best-effort: if Clerk
  // fails we fall back to email-only matching rather than 500.
  let orgIds: string[] = [];
  try {
    const client = await clerkClient();
    const memberships =
      await client.users.getOrganizationMembershipList({ userId });
    orgIds = (memberships?.data ?? [])
      .map((m) => m.organization?.id)
      .filter((id): id is string => Boolean(id));
  } catch {
    orgIds = [];
  }

  if (!email && orgIds.length === 0) {
    // No way to identify the user against any workspace.
    return NextResponse.json({ workspaces: [] });
  }

  const backendUrl = process.env.MISTERR_BACKEND_URL;
  const apiKey = process.env.MISTERR_WEB_API_KEY;
  if (!backendUrl || !apiKey) {
    return NextResponse.json({ workspaces: [] });
  }

  try {
    const url = new URL("/api/web/workspaces", backendUrl);
    if (email) url.searchParams.set("email", email);
    if (orgIds.length > 0) {
      url.searchParams.set("org_ids", orgIds.join(","));
    }
    const res = await fetch(url, {
      headers: { "x-misterr-web-token": apiKey },
      cache: "no-store",
    });
    if (!res.ok) {
      return NextResponse.json({ workspaces: [] });
    }
    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ workspaces: [] });
  }
}
