import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";
import { SignOutButton } from "@clerk/nextjs";

// Server-side gate on top of the middleware. The middleware enforces auth
// at the request boundary; this layout re-checks so any future server
// component inside this segment can rely on a non-null userId without
// re-querying. Belt + suspenders.
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
    <div className="flex min-h-screen">
      <aside className="flex w-56 flex-col justify-between border-r border-neutral-200 p-6 dark:border-neutral-800">
        <div className="text-xl font-semibold tracking-tight">Misterr</div>
        <SignOutButton>
          <button
            type="button"
            className="rounded-md border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700"
          >
            Sign out
          </button>
        </SignOutButton>
      </aside>
      <main className="flex-1 p-8">{children}</main>
    </div>
  );
}
