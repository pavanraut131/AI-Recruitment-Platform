"use client"

import { useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"

import { queryKeys } from "@/lib/api/query-keys"
import { onSessionEvent } from "@/lib/api/transport"

import { currentPath, isAuthPath, loginHref } from "./paths"
import { endSession } from "./session"

/**
 * Acts on the session problems any request reports (EPIC-02 §9.4;
 * `lib/api/transport.ts`). Mounted once, with the Query provider.
 *
 * - **401** (the session ended, or there never was one): clear the cache and
 *   go to `/login?next=<this page>`, with `reason=expired` when someone was
 *   signed in here. Never on the sign-in pages themselves, where a 401 from
 *   `/me` just means "not signed in" (no redirect loops).
 * - **403 "Choose an agency"**: refetch `/me`, so the auth gate sends the
 *   user to the agency picker.
 */
export function SessionWatcher() {
  const queryClient = useQueryClient()

  useEffect(
    () =>
      onSessionEvent((event) => {
        if (isAuthPath(window.location.pathname)) return
        if (event === "agency-required") {
          void queryClient.invalidateQueries({ queryKey: queryKeys.me.all })
          return
        }
        const wasSignedIn = queryClient.getQueryData(queryKeys.me.all) !== undefined
        endSession(queryClient, loginHref({ next: currentPath(), reason: wasSignedIn ? "expired" : undefined }))
      }),
    [queryClient],
  )

  return null
}
