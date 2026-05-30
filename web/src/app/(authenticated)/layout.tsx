import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

import { DashboardShell } from "./_components/dashboard-shell";

// Server-side gate on top of the middleware. The middleware enforces auth
// at the request boundary; this layout re-checks so any future server
// component inside this segment can rely on a non-null userId without
// re-querying. Belt + suspenders. Once we have a real user/workspace
// model in our DB, also bootstrap the AppUser <-> Clerk user link here.
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
  return <DashboardShell>{children}</DashboardShell>;
}
