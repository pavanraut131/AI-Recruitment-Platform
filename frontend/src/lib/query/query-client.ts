import { isServer, MutationCache, QueryCache, QueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { errorMessage, isApiProblem } from "@/lib/api/problem"

/**
 * Per-query / per-mutation options read by the global handlers below.
 *
 * - `errorToast`: show (`true`) or suppress (`false`) the error toast. Default:
 *   mutations always toast; queries toast only when a background refetch fails
 *   while data is shown (a first-load failure is the view's error state, spec §8).
 *   A form that maps 422 field errors onto its inputs passes `false`.
 */
export type AppQueryMeta = { errorToast?: boolean }

declare module "@tanstack/react-query" {
  interface Register {
    queryMeta: AppQueryMeta
    mutationMeta: AppQueryMeta
  }
}

/** Lists and details are fresh for 30 s: no refetch on remount or focus within that window. */
export const DEFAULT_STALE_TIME_MS = 30_000
/** Attempts after the first for a retryable failure (network or 5xx). */
export const MAX_RETRIES = 3

/**
 * Retry network failures and 5xx only. A 4xx (validation, not found) gives the
 * same answer every time, and anything that is not an `ApiProblem` is a bug in
 * our code, not a flaky server.
 */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= MAX_RETRIES) return false
  return isApiProblem(error) && (error.isNetworkError || error.isServerError)
}

export function showErrorToast(error: unknown): void {
  const { title, description } = errorMessage(error)
  // One toast per distinct message: ten queries failing together (server
  // down) show a single toast, not ten.
  toast.error(title, { description, id: `api-error:${title}:${description ?? ""}` })
}

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        const wanted = query.meta?.errorToast
        if (wanted === false) return
        if (wanted === true || query.state.data !== undefined) showErrorToast(error)
      },
    }),
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        if (mutation.meta?.errorToast === false) return
        showErrorToast(error)
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: DEFAULT_STALE_TIME_MS,
        retry: shouldRetry,
        // Active (mounted) views refetch stale data when the tab regains focus.
        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
      },
      mutations: {
        // Mutations are not idempotent in general (bulk delete, uploads).
        retry: false,
      },
    },
  })
}

let browserQueryClient: QueryClient | undefined

/**
 * The browser's single `QueryClient`, or a fresh one per server render (so
 * requests never share cached data). TanStack Query's recommended pattern for
 * the App Router: unlike `useState`, it survives React discarding the first
 * render when something suspends above a Suspense boundary.
 */
export function getQueryClient(): QueryClient {
  if (isServer) return makeQueryClient()
  browserQueryClient ??= makeQueryClient()
  return browserQueryClient
}
