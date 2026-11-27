"use client"

import { useEffect, useSyncExternalStore } from "react"

/** The `token` of a URL fragment (`#token=…`), or `null`. */
export function tokenFromHash(hash: string): string | null {
  const params = new URLSearchParams(hash.replace(/^#/, ""))
  const token = params.get("token")?.trim()
  return token ? token : null
}

// The token each page read, by path: it stays in memory after the fragment
// has been taken out of the address bar.
const readTokens = new Map<string, string | null>()

function readToken(): string | null {
  const { pathname, hash } = window.location
  const fromLink = tokenFromHash(hash)
  if (fromLink !== null) readTokens.set(pathname, fromLink)
  return readTokens.get(pathname) ?? null
}

function subscribe(onChange: () => void): () => void {
  // Another link opened in the same tab changes only the fragment.
  window.addEventListener("hashchange", onChange)
  return () => window.removeEventListener("hashchange", onChange)
}

/** Forget the tokens read so far (tests: each starts from a fresh page). */
export function forgetFragmentTokens(): void {
  readTokens.clear()
}

/**
 * The token of an emailed link (`/reset-password#token=…`,
 * `/accept-invite#token=…`, EPIC-02 §4.3). The fragment is never sent to a
 * server, so the token cannot reach access logs or a `Referer`; the page
 * reads it here, keeps it in memory and takes it out of the address bar, so
 * it is not left in the history or on screen. It is sent only in request
 * bodies. (Reloading the page after that finds no token: the link in the
 * email still works.)
 *
 * `undefined` until the page has read it (the server render cannot), then
 * the token or `null` when the link had none.
 */
export function useFragmentToken(): string | null | undefined {
  const token = useSyncExternalStore(subscribe, readToken, () => undefined)

  useEffect(() => {
    if (token && tokenFromHash(window.location.hash) !== null) {
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`)
    }
  }, [token])

  return token
}
