// Auth pages live under this route group so they share a dark, full-bleed
// shell distinct from the dashboard. Mirrors the visual structure of the
// Antiff (auth) layout: centered content + footer with legal links. Kept
// minimal — no next-intl, just hardcoded strings for now.

export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <main className="flex min-h-screen flex-col overflow-hidden bg-[#02090A] text-white">
      <div className="flex flex-1 flex-col items-center justify-center px-4 py-6">
        {children}
      </div>
      <footer className="px-4 pb-5">
        <p className="text-center text-[12px] tracking-[-0.05em] text-white/60">
          By continuing, you accept the{" "}
          <a
            href="/legal/terms"
            className="font-medium underline-offset-2 hover:underline"
          >
            terms
          </a>{" "}
          and{" "}
          <a
            href="/legal/privacy"
            className="font-medium underline-offset-2 hover:underline"
          >
            privacy policy
          </a>
          .
        </p>
      </footer>
    </main>
  );
}
