// White card centered on the dark auth bg. Ported from Antiff's AuthCard
// so the visual cadence matches: rounded-2xl, max-width 476px, subtle
// rise-in animation on mount.

export function AuthCard({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`auth-rise-d2 flex w-full max-w-[476px] flex-col items-center justify-center overflow-hidden rounded-2xl border border-[var(--color-border)] bg-white p-6 text-[var(--color-ink-deep)] ${className}`}
    >
      {children}
    </div>
  );
}
