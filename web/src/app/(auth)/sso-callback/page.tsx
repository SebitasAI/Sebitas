"use client";

import { AuthenticateWithRedirectCallback } from "@clerk/nextjs";

// Where Slack OAuth lands after the user authorizes. Clerk's helper
// finalises the session and routes to `redirectUrlComplete` from the
// caller (sign-in / sign-up). The component renders nothing user-visible.
export default function SSOCallbackPage() {
  return <AuthenticateWithRedirectCallback signInForceRedirectUrl="/dashboard" />;
}
