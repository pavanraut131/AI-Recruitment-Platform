/*
 * The sign-in screens' URLs, and where to send someone after signing in
 * (EPIC-02 §9.1). Shared by the auth pages, the auth gate, the session
 * watcher and `proxy.ts`.
 */

/** The pages for signed-out visitors (the `(auth)` route group, minus `/select-agency`, which needs a session). */
export const AUTH_PATHS = ["/login", "/forgot-password", "/reset-password", "/accept-invite"] as const

export const LOGIN_PATH = "/login"
export const SELECT_AGENCY_PATH = "/select-agency"

/** Why the login page was opened, shown above the form. */
export type LoginReason = "expired" | "password_changed"

/** Whether `pathname` is a page for signed-out visitors (a 401 there is expected, never a redirect). */
export function isAuthPath(pathname: string): boolean {
  return AUTH_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`))
}

const ORIGIN = "http://hireflow.invalid"

/**
 * Where to go after signing in: `next` when it is a path on this site (one
 * leading `/`, not `//host` or `/\host`, never a sign-in page), else `/`.
 * Never an absolute URL, so a crafted link cannot send someone elsewhere
 * (spec §10, no open redirect).
 */
export function safeNextPath(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/"
  let url: URL
  try {
    url = new URL(next, ORIGIN)
  } catch {
    return "/"
  }
  if (url.origin !== ORIGIN || isAuthPath(url.pathname)) return "/"
  return `${url.pathname}${url.search}${url.hash}`
}

/** `/login`, remembering where to come back to (`next`, omitted for `/`) and why (`reason`). */
export function loginHref({ next, reason }: { next?: string; reason?: LoginReason } = {}): string {
  const params = new URLSearchParams()
  const target = safeNextPath(next)
  if (target !== "/") params.set("next", target)
  if (reason) params.set("reason", reason)
  const query = params.toString()
  return query ? `${LOGIN_PATH}?${query}` : LOGIN_PATH
}

/** `/select-agency`, then on to `next`. */
export function selectAgencyHref(next?: string): string {
  const target = safeNextPath(next)
  return target === "/" ? SELECT_AGENCY_PATH : `${SELECT_AGENCY_PATH}?next=${encodeURIComponent(target)}`
}

/** The current page's path and query (not the fragment, which may hold a token). */
export function currentPath(): string {
  return `${window.location.pathname}${window.location.search}`
}
