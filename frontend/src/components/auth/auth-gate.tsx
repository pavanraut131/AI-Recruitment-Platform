"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"

import { LogoMark } from "@/components/app-shell/logo"
import { ErrorState } from "@/components/data-states"
import { currentPath, selectAgencyHref } from "@/lib/auth/paths"
import { isUnauthorized, useMe } from "@/lib/auth/session"

/**
 * The product's pages need a signed-in user with an active agency (EPIC-02
 * §9.4). The gate loads `GET /me` before anything inside it renders, so no
 * screen asks the API for data without a session:
 *
 * - signed in, with an agency: the app;
 * - signed in, no active agency: on to `/select-agency`;
 * - not signed in (401): the session watcher sends the page to `/login?next=…`
 *   (`proxy.ts` usually redirected before the page loaded at all);
 * - the API unreachable: an error with a retry.
 *
 * `/me` is the only proof of a session; the cookie is HttpOnly and the page
 * never sees it. A page restored from the back-forward cache (say, after
 * signing out) is reloaded, so it asks `/me` again instead of showing what
 * the last user saw.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const me = useMe()
  const needsAgency = me.data !== undefined && me.data.active_agency === null

  useEffect(() => {
    if (needsAgency) router.replace(selectAgencyHref(currentPath()))
  }, [needsAgency, router])

  useEffect(() => {
    const reloadIfRestored = (event: PageTransitionEvent) => {
      if (event.persisted) window.location.reload()
    }
    window.addEventListener("pageshow", reloadIfRestored)
    return () => window.removeEventListener("pageshow", reloadIfRestored)
  }, [])

  if (me.data !== undefined && !needsAgency) return children
  if (me.isError && !isUnauthorized(me.error)) {
    return (
      <main id="main-content" tabIndex={-1} className="flex min-h-svh items-center justify-center px-4 outline-none">
        <ErrorState
          error={me.error}
          onRetry={() => void me.refetch()}
          retrying={me.isFetching}
          className="w-full max-w-md border-t-0"
        />
      </main>
    )
  }
  return <GateLoading />
}

/** While `/me` loads: the mark only, faded in late so a fast answer shows nothing at all. */
function GateLoading() {
  return (
    <div aria-busy="true" className="flex min-h-svh items-center justify-center">
      <span className="sr-only">Loading HireFlow AI</span>
      <LogoMark className="size-9 animate-in fill-mode-both fade-in delay-500" />
    </div>
  )
}
