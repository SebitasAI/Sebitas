import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

import { QueryClientShell } from "@/lib/query-client";
import { DashboardShell } from "./_components/dashboard-shell";
import { InstallGate } from "./_components/install-gate";

// Server-side gate on top of the middleware. The middleware enforces auth
// at the request boundary; this layout re-checks so any future server
// component inside this segment can rely on a non-null userId without
// re-querying. Belt + suspenders.
//
// On top of auth, `InstallGate` covers the dashboard with a forced
// "install Misterr on Slack" modal until the user has at least one
// workspace where the bot is installed. Without that there's nothing
// useful for them to do inside the app.
//
// User profile data (name, email, avatar) is read client-side from Clerk
// inside `SidebarUserDropdown` via useUser(), so this layout doesn't need
// to pass it down.
export default async function AuthenticatedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { userId } = await auth();
  if (!userId) {
    redirect("/sign-in");
  }
  return (
    <QueryClientShell>
      <InstallGate>
        <DashboardShell>{children}</DashboardShell>
      </InstallGate>
    </QueryClientShell>
  );
}
