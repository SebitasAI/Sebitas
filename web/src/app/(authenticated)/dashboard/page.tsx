import { currentUser } from "@clerk/nextjs/server";

// Server component: pulls the Clerk user via the cookie session the
// middleware seeded. No client-side fetch, no useEffect; the page renders
// the greeting at request time and ships HTML straight away.
export default async function DashboardPage() {
  const user = await currentUser();
  const firstName = user?.firstName ?? "there";
  return (
    <section>
      <h1 className="text-2xl font-semibold">Hola, {firstName}.</h1>
    </section>
  );
}
