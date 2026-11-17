import createClient, { type Middleware } from "openapi-fetch"

import { networkProblem, problemFromResponse } from "./problem"
import type { components, operations, paths } from "./schema"

export type { components, operations, paths } from "./schema"
export { ApiProblem, errorMessage, isApiProblem, type FieldError } from "./problem"

/** A schema of the v2 document by name, e.g. `Schema<"Campaign">`. */
export type Schema<Name extends keyof components["schemas"]> = components["schemas"][Name]

/** The query parameters of an operation, e.g. `QueryParams<"listCampaigns">`. */
export type QueryParams<Operation extends keyof operations> = NonNullable<
  operations[Operation]["parameters"]["query"]
>

const DEFAULT_API_URL = "http://localhost:8000"

/**
 * Root of the `/api/v2` application (ADR 0008). The generated paths are
 * relative to it (`/campaigns`, …), as the document's `servers` entry says.
 */
export const API_V2_BASE_URL = `${(process.env.NEXT_PUBLIC_API_URL || DEFAULT_API_URL).replace(/\/+$/, "")}/api/v2`

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
 * `fetch` is resolved on each request rather than captured at creation, so
 * tests can stub `globalThis.fetch`.
 */
export function createApiClient(baseUrl: string = API_V2_BASE_URL) {
  const client = createClient<paths>({
    baseUrl,
    fetch: (request) => globalThis.fetch(request),
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
