import { redirect } from "next/navigation";
import { auth } from "@clerk/nextjs/server";

// Entry: if the user has a session, send them to /dashboard; otherwise the
// middleware would already redirect to /sign-in for protected routes, but
// because `/` is in the protected matcher this branch only fires when the
// session check passes server-side. We never render content here on purpose.
export default async function RootRedirect() {
  const { userId } = await auth();
  redirect(userId ? "/dashboard" : "/sign-in");
}
