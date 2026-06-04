"use client";

import { useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// One QueryClient per browser tab. Constructing it inside the component
// (lazily, via useState) avoids the Next.js Fast Refresh / SSR pitfall
// where a module-level singleton gets reused across requests on the
// server.
//
// Cache tuning rationale (2026-06-03 perf pass):
//
//   staleTime: 60s    — A query result is considered fresh for one minute
//                       after the fetch. While fresh, react-query returns
//                       the cached value synchronously and skips the
//                       network call entirely. This is what makes a tab
//                       switch (Dashboard -> Skills -> Dashboard) feel
//                       instant: the second visit reads from cache.
//
//   gcTime: 5 min     — Once a query has no active subscribers (the user
//                       navigated away), the cached entry survives this
//                       long before garbage-collected. Without it the
//                       default 5min is fine, but spelled out so a future
//                       refactor doesn't silently lower it.
//
//   refetchOnMount:   — 'always' would refetch even when fresh; we leave
//   default (true)     it as the library default (refetch only if stale).
//                       That keeps freshness honest while letting the
//                       60s window absorb most rapid navigation.
//
//   refetchOnWindowFocus: false — the Scheduled Tasks page used to spam
//                       the API every alt-tab. Disabled globally so
//                       background tabs don't fan out requests.
//
//   retry: 1          — one quiet retry covers transient network blips
//                       without delaying the user further on a real
//                       failure.
export function QueryClientShell({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60_000,
            gcTime: 5 * 60_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
          mutations: {
            retry: 0,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
