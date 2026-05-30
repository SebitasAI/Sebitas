import { currentUser } from "@clerk/nextjs/server";

import { HomeIcon } from "../_components/nav-icons";
import { PageBody, PageHeader } from "../_components/page-header";

// Dashboard home. Server component: pulls the Clerk user via the cookie
// session the middleware seeded. PageHeader uses the same filled HomeIcon
// the sidebar uses (sidebar icon and header icon must be consistent —
// previous mismatch came from sidebar-lucide vs header-lucide rendering
// at different stroke weights). Body is intentionally minimal until we
// wire real widgets (tasks, recent activity, etc.).
export default async function DashboardPage() {
  const user = await currentUser();
  const firstName = user?.firstName ?? "there";
  return (
    <>
      <PageHeader title="Home" Icon={HomeIcon} />
      <PageBody>
        <h1 className="text-2xl font-semibold tracking-tight">
          Hola, {firstName} <span aria-hidden>👋</span>
        </h1>
      </PageBody>
    </>
  );
}
