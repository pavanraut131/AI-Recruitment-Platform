import { API_V2_BASE_URL } from "./base-url"
import { CSRF_HEADER, csrfToken, fetchCsrfToken, isCsrfFailure, isUnsafeMethod } from "./csrf"
import { problemFromResponse } from "./problem"

/*
 * How `/api/v2` requests carry the session (EPIC-02 spec §4.1, §4.5, §9.4).
 *
 * - Credentials: the session is an HttpOnly cookie on the API's host. Every
 *   request is sent with `credentials: "include"` (the generated client),
 *   `withCredentials` (the upload XHR, the event stream).
 * - CSRF: unsafe requests carry `X-CSRF-Token` (`csrf.ts`). A CSRF 403 is
 *   repeated once with a freshly fetched token.
 * - Session problems are reported to whoever listens ({@link onSessionEvent};
 *   the app's `SessionWatcher`, which owns navigation and the Query cache):
 *   - `unauthorized`: a 401 from anything but the sign-in endpoints
 *     (`/auth/…`, where a 401 is a wrong password, not an ended session);
 *   - `agency-required`: a 403 "Choose an agency" (the session has no active
 *     agency, or lost access to it).
 */

export type SessionEvent = "unauthorized" | "agency-required"
type SessionListener = (event: SessionEvent) => void

/** The detail of the API's 403 for a session without an active agency (`identity.dependencies.CHOOSE_AGENCY`). */
export const CHOOSE_AGENCY_DETAIL = "Choose an agency"

const listeners = new Set<SessionListener>()

/** Listen for session problems; returns the unsubscribe function. */
export function onSessionEvent(listener: SessionListener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function emit(event: SessionEvent): void {
  for (const listener of listeners) listener(event)
}

/** The path of an API URL relative to `/api/v2` (`/auth/login`), or the URL's whole path when it is not under it. */
function apiPath(url: string, baseUrl: string): string {
  const path = new URL(url).pathname
  const base = new URL(baseUrl).pathname.replace(/\/+$/, "")
  return path.startsWith(`${base}/`) ? path.slice(base.length) : path
}

/** Sign-in, sign-out, reset and invitation endpoints answer 401 for their own reasons. */
function isAuthEndpoint(path: string): boolean {
  return path.startsWith("/auth/")
}

/**
 * Reports a response's session problem, if it has one (see the module
 * comment). Used by the client's fetch and by the upload XHR.
 */
export async function reportSessionProblems(response: Response, url: string, baseUrl: string = API_V2_BASE_URL): Promise<void> {
  if (response.status !== 401 && response.status !== 403) return
  if (isAuthEndpoint(apiPath(url, baseUrl))) return
  if (response.status === 401) {
    emit("unauthorized")
    return
  }
  const problem = await problemFromResponse(response)
  if (problem.detail === CHOOSE_AGENCY_DETAIL) emit("agency-required")
}

/**
 * The generated client's `fetch`: sends `request` with the CSRF header when it
 * is unsafe, repeats it once with a fresh token after a CSRF 403, and reports
 * session problems. Credentials come from the client's options.
 */
export async function sendApiRequest(
  request: Request,
  { baseUrl = API_V2_BASE_URL, send = (r: Request) => globalThis.fetch(r) }: { baseUrl?: string; send?: (request: Request) => Promise<Response> } = {},
): Promise<Response> {
  if (!isUnsafeMethod(request.method)) {
    const response = await send(request)
    await reportSessionProblems(response, request.url, baseUrl)
    return response
  }
  // The body can be read once: keep a copy for the repeat.
  const spare = request.clone()
  request.headers.set(CSRF_HEADER, await csrfToken(baseUrl))
  let response = await send(request)
  if (await isCsrfFailure(response)) {
    spare.headers.set(CSRF_HEADER, await fetchCsrfToken(baseUrl))
    response = await send(spare)
  }
  await reportSessionProblems(response, request.url, baseUrl)
  return response
}
