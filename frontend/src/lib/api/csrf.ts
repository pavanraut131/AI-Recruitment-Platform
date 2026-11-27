import { API_V2_BASE_URL } from "./base-url"
import { networkProblem, problemFromResponse } from "./problem"
import type { components } from "./schema"

/*
 * CSRF tokens for `/api/v2` (EPIC-02 spec §4.5, §9.4).
 *
 * The API checks a signed double-submit token on every unsafe request
 * (POST, PUT, PATCH, DELETE): the `X-CSRF-Token` header must equal its CSRF
 * cookie and be signed for the browser's session. The cookie is readable
 * (not HttpOnly), so the token comes from it whenever this page can see it
 * (the API on the same host, as in development, the E2E stack and a
 * one-origin deployment). A frontend on another host cannot read the API's
 * cookies; it uses the token the API also returns in its bodies
 * (`GET /auth/csrf`, `Me.csrf_token`), remembered here.
 *
 * The token is bound to the session, so it changes at every sign-in. A stale
 * one is answered with a 403 ("CSRF token missing or invalid"); the client
 * then fetches a fresh one and repeats the request once (`transport.ts`).
 */

export const CSRF_HEADER = "X-CSRF-Token"
/** `__Host-hf_csrf` with Secure cookies (deployed), `hf_csrf` over plain http (development, E2E). */
export const CSRF_COOKIE_NAMES = ["__Host-hf_csrf", "hf_csrf"] as const
/** The detail of the API's 403 for a missing or wrong token (`app.platform.csrf.FAILURE_DETAIL`). */
export const CSRF_FAILURE_DETAIL = "CSRF token missing or invalid"

const UNSAFE_METHODS: ReadonlySet<string> = new Set(["POST", "PUT", "PATCH", "DELETE"])

/** Methods that change something, so the API wants the CSRF header. */
export function isUnsafeMethod(method: string): boolean {
  return UNSAFE_METHODS.has(method.toUpperCase())
}

let remembered: string | null = null
let inflight: Promise<string> | null = null

/** The CSRF cookie's value, when this page can read it. */
export function readCsrfCookie(cookies: string = typeof document === "undefined" ? "" : document.cookie): string | null {
  const values = new Map<string, string>()
  for (const part of cookies.split(";")) {
    const index = part.indexOf("=")
    if (index < 0) continue
    values.set(part.slice(0, index).trim(), part.slice(index + 1).trim())
  }
  for (const name of CSRF_COOKIE_NAMES) {
    const value = values.get(name)
    if (value) return decodeURIComponent(value)
  }
  return null
}

/** The token to send now without asking the API: the cookie's, else the last one the API returned. */
export function currentCsrfToken(): string | null {
  return readCsrfCookie() ?? remembered
}

/** Keep a token the API returned in a body (`Me.csrf_token`), for when the cookie is not readable. */
export function rememberCsrfToken(token: string | null | undefined): void {
  if (token) remembered = token
}

/** Drop the remembered token (sign-out, an ended session). */
export function forgetCsrfToken(): void {
  remembered = null
}

type CsrfTokenBody = components["schemas"]["CsrfToken"]

/**
 * A fresh token from `GET /auth/csrf` (which also sets the cookie); remembered.
 * Calls made while one is in flight share it. Rejects with an `ApiProblem`.
 */
export function fetchCsrfToken(baseUrl: string = API_V2_BASE_URL): Promise<string> {
  inflight ??= requestToken(baseUrl).finally(() => {
    inflight = null
  })
  return inflight
}

async function requestToken(baseUrl: string): Promise<string> {
  let response: Response
  try {
    response = await globalThis.fetch(new Request(`${baseUrl}/auth/csrf`, { credentials: "include" }))
  } catch (error) {
    throw networkProblem(error)
  }
  if (!response.ok) throw await problemFromResponse(response)
  const body = (await response.json()) as CsrfTokenBody
  remembered = body.csrf_token
  return body.csrf_token
}

/** The token for an unsafe request: {@link currentCsrfToken}, else a fetched one. */
export async function csrfToken(baseUrl: string = API_V2_BASE_URL): Promise<string> {
  return currentCsrfToken() ?? fetchCsrfToken(baseUrl)
}

/** Whether a response is the API's CSRF refusal (a 403 with {@link CSRF_FAILURE_DETAIL}). */
export async function isCsrfFailure(response: Response): Promise<boolean> {
  if (response.status !== 403) return false
  const problem = await problemFromResponse(response)
  return problem.detail === CSRF_FAILURE_DETAIL
}
