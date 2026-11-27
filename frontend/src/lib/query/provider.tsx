"use client"

import { QueryClientProvider } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"
import { NuqsAdapter } from "nuqs/adapters/next/app"

import { SessionWatcher } from "@/lib/auth/session-watcher"

import { getQueryClient } from "./query-client"

/**
 * Client-side data providers for every route (ADR 0007): TanStack Query for
 * server state, nuqs for URL state, and the watcher that signs the page out
 * when a request finds the session ended (EPIC-02 §9.4). The Query devtools
 * render only in development (`next dev`); production builds drop them.
 */
export function DataProviders({ children }: { children: React.ReactNode }) {
  const queryClient = getQueryClient()
  return (
    <QueryClientProvider client={queryClient}>
      <SessionWatcher />
      <NuqsAdapter>{children}</NuqsAdapter>
      {process.env.NODE_ENV === "development" && (
        <ReactQueryDevtools initialIsOpen={false} buttonPosition="bottom-left" />
      )}
    </QueryClientProvider>
  )
}
