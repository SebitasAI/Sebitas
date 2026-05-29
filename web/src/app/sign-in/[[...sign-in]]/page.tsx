import { SignIn } from "@clerk/nextjs";

// Catch-all route so Clerk can mount its multi-step flow (/sign-in,
// /sign-in/factor-one, etc.) under the same page.
export default function SignInPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      <SignIn />
    </main>
  );
}
