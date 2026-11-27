import createClient, { type Middleware } from "openapi-fetch"

import { API_V2_BASE_URL } from "./base-url"
import { isApiProblem, networkProblem, problemFromResponse } from "./problem"
import type { components, operations, paths } from "./schema"
import { sendApiRequest } from "./transport"

export type { components, operations, paths } from "./schema"
export { API_URL, API_V2_BASE_URL } from "./base-url"
export { ApiProblem, errorMessage, isApiProblem, type FieldError } from "./problem"

/** A schema of the v2 document by name, e.g. `Schema<"Campaign">`. */
export type Schema<Name extends keyof components["schemas"]> = components["schemas"][Name]

/** The query parameters of an operation, e.g. `QueryParams<"listCampaigns">`. */
export type QueryParams<Operation extends keyof operations> = NonNullable<
  operations[Operation]["parameters"]["query"]
>

/**
 * Turns every failure into a thrown {@link ApiProblem}: a non-2xx response
 * (parsed from its `application/problem+json` body) and a request that got
 * no response at all (status 0). An aborted request (TanStack Query cancelling
 * a query) is rethrown unchanged, so it is not reported as a network error.
 */
export const problemMiddleware: Middleware = {
  async onResponse({ response }) {
    if (!response.ok) throw await problemFromResponse(response)
    return undefined
  },
  onError({ error }) {
    if (isAbort(error)) return error instanceof Error ? error : undefined
    // Already a problem: e.g. the CSRF token could not be fetched before the request.
    if (isApiProblem(error)) return error
    return networkProblem(error)
  },
}

function isAbort(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error && error.name === "AbortError"
}

/**
 * A typed client for `/api/v2` (openapi-fetch over the generated `paths`,
 * ADR 0006). Failures throw an `ApiProblem`, so `data` is the 2xx body
 * (narrow it with {@link unwrap}).
 *
 * Every request carries the session cookie (`credentials: "include"`); unsafe
 * ones carry the CSRF token, and are repeated once after a CSRF 403; a 401
 * is reported as an ended session (`transport.ts`, EPIC-02 §9.4).
 *
 * `fetch` is resolved on each request rather than captured at creation, so
 * tests can stub `globalThis.fetch`.
 */
export function createApiClient(baseUrl: string = API_V2_BASE_URL) {
  const client = createClient<paths>({
    baseUrl,
    credentials: "include",
    fetch: (request) => sendApiRequest(request, { baseUrl }),
  })
  client.use(problemMiddleware)
  return client
}

/** The app's `/api/v2` client. */
export const api = createApiClient()

/**
 * The `data` of a client call, typed as present. Failures already threw an
 * `ApiProblem` in the middleware; this only narrows the type (and guards
 * against a client built without the middleware).
 *
 * ```ts
 * queryFn: ({ signal }) => api.GET("/campaigns", { params: { query }, signal }).then(unwrap)
 * ```
 */
export function unwrap<Data>(result: { data?: Data; error?: unknown; response: Response }): Data {
  if (!result.response.ok) {
    throw result.error ?? new Error(`Unexpected ${result.response.status} response`)
  }
  return result.data as Data
}
