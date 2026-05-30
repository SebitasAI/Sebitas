"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useSignUp } from "@clerk/nextjs/legacy";

import { AuthCard } from "../../_components/auth-card";
import { AuthLogo } from "../../_components/auth-logo";
import { SlackButton } from "../../_components/slack-button";

// Slack-only sign-up. Visually identical to sign-in (single Slack button)
// because OAuth doesn't have a real "sign-up vs sign-in" distinction —
// Clerk decides based on whether the Slack account already maps to a
// user. We keep separate routes so URLs in marketing emails / CTAs can
// distinguish them.
export default function SignUpPage() {
  const router = useRouter();
  const { isSignedIn } = useAuth();
  const { isLoaded, signUp } = useSignUp();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (isSignedIn) router.replace("/dashboard");
  }, [isSignedIn, router]);

  const onSlack = async () => {
    if (!isLoaded || !signUp) return;
    setError(null);
    setSubmitting(true);
    try {
      await signUp.authenticateWithRedirect({
        strategy: "oauth_slack",
        redirectUrl: "/sso-callback",
        redirectUrlComplete: "/dashboard",
      });
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "No pude crear la cuenta con Slack.",
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
              Creá tu cuenta de Misterr
            </h1>
            <p className="text-base tracking-[-0.05em]">
              Conectate con tu workspace de Slack para empezar.
            </p>
          </div>

          <div className="auth-fade flex w-full flex-col items-center gap-5">
            <SlackButton onClick={onSlack} disabled={!isLoaded || submitting}>
              {submitting ? "Redirigiendo a Slack…" : "Continuar con Slack"}
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
            <span>¿Ya tenés cuenta?</span>
            <Link
              href="/sign-in"
              className="text-[var(--color-link-blue)] hover:underline"
            >
              Iniciá sesión
            </Link>
          </div>
        </div>
      </AuthCard>
    </div>
  );
}
