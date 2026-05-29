// Static-exportable: pure server component, no cookies/headers/runtime.
// All copy is placeholder; replace when branding lands.
export default function HomePage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center px-6 py-24 text-center">
      <h1 className="text-6xl font-semibold tracking-tight sm:text-7xl">
        Misterr
      </h1>
      <p className="mt-6 max-w-xl text-lg text-neutral-600 dark:text-neutral-300">
        AI coworker that lives in your Slack.
      </p>
      <button
        type="button"
        disabled
        aria-disabled="true"
        className="mt-10 cursor-not-allowed rounded-full border border-neutral-300 px-6 py-2 text-sm font-medium text-neutral-500 dark:border-neutral-700 dark:text-neutral-400"
      >
        Coming soon
      </button>
    </main>
  );
}
