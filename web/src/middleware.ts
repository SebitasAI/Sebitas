import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

// Public routes: anything in the sign-in / sign-up flow plus the favicon &
// other static asset paths Clerk shouldn't gate. Everything else requires
// an authenticated session via auth.protect().
const isPublicRoute = createRouteMatcher([
  "/sign-in(.*)",
  "/sign-up(.*)",
  // SSO callback lands here after Slack OAuth; Clerk's
  // AuthenticateWithRedirectCallback needs to run before the user has a
  // session, so this path must not be gated.
  "/sso-callback(.*)",
]);

export default clerkMiddleware(async (auth, request) => {
  if (!isPublicRoute(request)) {
    await auth.protect();
  }
});

// Run on every path EXCEPT Next.js internals + static files (Clerk's
// recommended matcher from the docs). The pattern intentionally leaves
// API routes in the matcher so server actions / route handlers also
// get the middleware.
export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
    "/(api|trpc)(.*)",
  ],
};
