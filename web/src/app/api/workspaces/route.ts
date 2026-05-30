import { NextResponse } from "next/server";
import { auth, currentUser } from "@clerk/nextjs/server";

// Returns the workspaces where Misterr is installed AND the current Clerk
// user is a Slack member (matched by email against the cached SlackUser
// roster). Resolves the user server-side so the browser can't forge
// requests for someone else's email.
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
  if (!email) {
    return NextResponse.json({ workspaces: [] });
  }

  const backendUrl = process.env.MISTERR_BACKEND_URL;
  const apiKey = process.env.MISTERR_WEB_API_KEY;
  if (!backendUrl || !apiKey) {
    return NextResponse.json({ workspaces: [] });
  }

  try {
    const url = new URL("/api/web/workspaces", backendUrl);
    url.searchParams.set("email", email);
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
