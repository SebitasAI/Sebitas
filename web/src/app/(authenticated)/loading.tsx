// Suspense boundary for the entire authenticated segment.
//
// Next.js renders this file the moment the user navigates to any
// `(authenticated)` subpage while its server components / dynamic
// imports are still loading. Without this file the browser stays on
// the previous page (or a blank screen on first paint) until the new
// route's JS arrives -- which is the main "the app loads slow" symptom.
//
// What this renders: a header chrome + a single shimmer card, sized to
// roughly match the post-load page so the layout doesn't jump. The
// real PageHeader / PageBody come from the inner page once data is
// ready; this is just visual sugar to absorb the gap.

export default function AuthenticatedLoading() {
  return (
    <div className="flex h-full w-full flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-border)] bg-white px-6 py-4 md:px-12">
        <div className="flex items-center gap-2.5">
          <span
            aria-hidden
            className="inline-flex size-6 items-center justify-center rounded-md bg-[#FFE5D6]"
          />
          <span
            aria-hidden
            className="inline-block h-4 w-24 animate-pulse rounded bg-neutral-100"
          />
        </div>
      </header>
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-3 px-6 py-5 md:px-12">
        <div className="h-5 w-44 animate-pulse rounded bg-neutral-100" />
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="h-24 animate-pulse rounded-xl border border-[var(--color-border)] bg-white"
            />
          ))}
        </div>
        <div className="mt-3 h-48 animate-pulse rounded-xl border border-[var(--color-border)] bg-white" />
      </div>
    </div>
  );
}
