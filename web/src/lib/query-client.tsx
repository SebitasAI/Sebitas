"use client";

import { useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// One QueryClient per browser tab. Constructing it inside the component
// (lazily, via useState) avoids the Next.js Fast Refresh / SSR pitfall
// where a module-level singleton gets reused across requests on the
// server. Defaults: 30s staleTime so a tab switch doesn't immediately
// refetch; retry once on network errors; refetchOnWindowFocus off so the
// Scheduled Tasks page doesn't spam the API every time the user alt-tabs.
export function QueryClientShell({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
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
