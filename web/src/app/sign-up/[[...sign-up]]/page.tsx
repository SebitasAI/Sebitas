import { SignUp } from "@clerk/nextjs";

// Catch-all route so Clerk can mount its multi-step flow under the same page.
export default function SignUpPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      <SignUp />
    </main>
  );
}
