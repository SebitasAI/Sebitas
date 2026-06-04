"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useSignIn } from "@clerk/nextjs/legacy";

import { AuthCard } from "../../_components/auth-card";
import { AuthLogo } from "../../_components/auth-logo";
import { SlackButton } from "../../_components/slack-button";

// Slack-only sign-in. Mirrors Antiff's visual layout (logo + card + footer)
// but the form is a single "Continue with Slack" button: no email/password,
// no other social providers. Clerk's OAuth handles new vs returning user
// automatically — the same button serves both cases.
export default function SignInPage() {
  const router = useRouter();
  const { isSignedIn } = useAuth();
  const { isLoaded, signIn } = useSignIn();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (isSignedIn) router.replace("/dashboard");
  }, [isSignedIn, router]);

  const onSlack = async () => {
    if (!isLoaded || !signIn) return;
    setError(null);
    setSubmitting(true);
    try {
      await signIn.authenticateWithRedirect({
        strategy: "oauth_slack",
        redirectUrl: "/sso-callback",
        redirectUrlComplete: "/dashboard",
      });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Couldn't sign in with Slack.",
      );
      setSubmitting(false);
    }
  };

  return (
    <div className="flex w-full flex-col items-center gap-6">
      <AuthLogo />
      <AuthCard>
        <div className="flex w-full flex-col items-center gap-5 py-2.5">
          <div className="flex w-full flex-col gap-2.5 py-2.5 text-center">
            <h1 className="text-[24px] font-medium leading-tight tracking-[-0.05em]">
              Sign in to Misterr
            </h1>
            <p className="text-base tracking-[-0.05em]">
              Connect with your Slack account to continue.
            </p>
          </div>

          <div className="auth-fade flex w-full flex-col items-center gap-5">
            <SlackButton onClick={onSlack} disabled={!isLoaded || submitting}>
              {submitting ? "Redirecting to Slack…" : "Continue with Slack"}
            </SlackButton>
            {error ? (
              <p
                role="alert"
                className="w-full text-[12px] tracking-[-0.05em] text-red-600"
              >
                {error}
              </p>
            ) : null}
          </div>

          <div className="flex w-full items-center justify-center gap-2 pt-5 text-[12px] tracking-[-0.05em]">
            <span>Don&apos;t have an account?</span>
            <Link
              href="/sign-up"
              className="text-[var(--color-link-blue)] hover:underline"
            >
              Create one
            </Link>
          </div>
        </div>
      </AuthCard>
    </div>
  );
}
