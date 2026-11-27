import { NextResponse, type NextRequest } from "next/server"

import { API_URL } from "@/lib/api/base-url"
import { isAuthPath, loginHref, SELECT_AGENCY_PATH } from "@/lib/auth/paths"

/*
 * An optimistic redirect to the login page (EPIC-02 §9.4; Next.js 16's Proxy,
 * formerly Middleware: "optimistic checks" in the authentication guide).
 *
 * A request for a product page **without** a session cookie cannot be signed
 * in, so it is redirected to `/login?next=<page>` before anything renders.
 * A cookie proves nothing (it may have expired or been revoked): such requests
 * pass, and the auth gate's `GET /me` decides. The API checks every request
 * anyway; this only saves a signed-out visitor a blank page and a round trip.
 *
 * The session cookie is host-only on the API's host. The browser sends it
 * with page requests too only when the API and the pages share a host (ports
 * do not matter for cookies): development, the E2E stack, and a deployment on
 * one origin (spec §4.1). Otherwise the proxy cannot see it, so it stays out
 * of the way; redirecting would bounce a signed-in user between `/login` and
 * the page forever.
 */

/** `__Host-hf_session` with Secure cookies (deployed), `hf_session` over plain http. */
const SESSION_COOKIES = ["__Host-hf_session", "hf_session"] as const

function hostname(value: string | null): string | null {
  if (!value) return null
  try {
    return new URL(`http://${value}`).hostname
  } catch {
    return null
  }
}

/** Whether the browser sends the API's host-only cookies along with this page request. */
export function seesSessionCookie(request: NextRequest, apiUrl: string = API_URL): boolean {
  const pageHost = hostname(request.headers.get("host")) ?? request.nextUrl.hostname
  return new URL(apiUrl).hostname === pageHost
}

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl
  if (isAuthPath(pathname) || pathname === SELECT_AGENCY_PATH) return NextResponse.next()
  if (!seesSessionCookie(request)) return NextResponse.next()
  if (SESSION_COOKIES.some((name) => request.cookies.has(name))) return NextResponse.next()
  return NextResponse.redirect(new URL(loginHref({ next: `${pathname}${search}` }), request.url))
}

export const config = {
  // Pages only: not Next's own files, the `/api` rewrite to the backend, or icons.
  matcher: ["/((?!_next/static|_next/image|api/|favicon\\.ico|icon\\.svg|.*\\.(?:png|jpg|jpeg|svg|webp|ico|txt|xml)$).*)"],
}
