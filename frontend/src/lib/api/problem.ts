import type { components } from "./schema"

/** One invalid field of a 422, as the API sends it (`loc` is the path, e.g. `["body", "title"]`). */
export type FieldError = components["schemas"]["FieldError"]

export const PROBLEM_MEDIA_TYPE = "application/problem+json"

/** `status` of an {@link ApiProblem} for a request that got no HTTP response at all. */
export const NETWORK_ERROR_STATUS = 0

type ApiProblemInit = {
  status: number
  title: string
  type?: string
  detail?: string
  errors?: FieldError[]
  retryAfter?: number
  cause?: unknown
}

/**
 * Every failed `/api/v2` request, as an RFC 9457 problem (spec §6.1, ADR 0006).
 *
 * The client's middleware throws one for any non-2xx response (parsed from the
 * `application/problem+json` body, or built from the status when the body is
 * something else, e.g. a proxy's HTML 502) and for a request that never got a
 * response (`status` 0, {@link isNetworkError}). Query retries and the toasts
 * are decided from it, and forms map {@link fieldErrors} onto their fields.
 */
export class ApiProblem extends Error {
  override readonly name = "ApiProblem"
  /** HTTP status, or {@link NETWORK_ERROR_STATUS} (0) when there was no response. */
  readonly status: number
  /** Short summary (the HTTP status phrase for plain HTTP errors). */
  readonly title: string
  /** URI reference identifying the problem type; `about:blank` for a plain HTTP error. */
  readonly type: string
  /** Explanation specific to this occurrence, when the API gave one. */
  readonly detail: string | undefined
  /** One entry per invalid field (422 only); empty otherwise. */
  readonly errors: readonly FieldError[]
  /**
   * Seconds to wait before trying again, from the `Retry-After` header (a 429
   * from the rate limits, EPIC-02 §4.6); `undefined` when the response had none.
   */
  readonly retryAfter: number | undefined

  constructor({ status, title, type = "about:blank", detail, errors = [], retryAfter, cause }: ApiProblemInit) {
    super(detail ?? title, cause === undefined ? undefined : { cause })
    this.status = status
    this.title = title
    this.type = type
    this.detail = detail
    this.errors = errors
    this.retryAfter = retryAfter
  }

  /** The request never got a response (offline, DNS, CORS, server down). */
  get isNetworkError(): boolean {
    return this.status === NETWORK_ERROR_STATUS
  }

  /** 4xx: the request was wrong; repeating it will not help. */
  get isClientError(): boolean {
    return this.status >= 400 && this.status < 500
  }

  /** 5xx: the server failed; the same request may succeed later. */
  get isServerError(): boolean {
    return this.status >= 500
  }

  /**
   * The field errors keyed by their path without the location, dot-separated
   * (`["body", "required_skills", 0]` → `"required_skills.0"`), as
   * react-hook-form names fields. A path that is only a location (`["body"]`,
   * the whole body is invalid) is keyed `""`. The first message per path wins.
   */
  fieldErrors(): Record<string, string> {
    const byPath: Record<string, string> = {}
    for (const { loc, msg } of this.errors) {
      const path = loc.slice(1).join(".")
      if (!(path in byPath)) byPath[path] = msg
    }
    return byPath
  }
}

export function isApiProblem(error: unknown): error is ApiProblem {
  return error instanceof ApiProblem
}

/** A request that got no response. `cause` keeps the original error (e.g. fetch's TypeError). */
export function networkProblem(cause?: unknown): ApiProblem {
  return new ApiProblem({
    status: NETWORK_ERROR_STATUS,
    title: "Network error",
    detail: "Could not reach the HireFlow AI server. Check your connection and try again.",
    cause,
  })
}

/**
 * The problem a failed response describes. Reads a clone, so the response's
 * own body stays unread. Never throws: a body that is not a problem (or not
 * JSON) gives a problem built from the status line.
 */
export async function problemFromResponse(response: Response): Promise<ApiProblem> {
  const fallback = {
    status: response.status,
    title: response.statusText || `HTTP ${response.status}`,
    retryAfter: parseRetryAfter(response.headers.get("Retry-After")),
  }
  const contentType = response.headers.get("Content-Type") ?? ""
  if (!contentType.includes("json")) return new ApiProblem(fallback)
  let body: unknown
  try {
    body = await response.clone().json()
  } catch {
    return new ApiProblem(fallback)
  }
  return new ApiProblem({ ...fallback, ...problemFields(body) })
}

/**
 * A `Retry-After` value in seconds: either delay-seconds or an HTTP date
 * (RFC 9110 §10.2.3). The API sends seconds; it is readable cross-origin
 * because the CORS policy exposes it.
 */
export function parseRetryAfter(value: string | null, now: number = Date.now()): number | undefined {
  if (value === null) return undefined
  const text = value.trim()
  if (/^\d+$/.test(text)) return Number(text)
  const date = Date.parse(text)
  if (Number.isNaN(date)) return undefined
  return Math.max(0, Math.ceil((date - now) / 1000))
}

/** The problem fields of a JSON body, each only when it has the right type. */
function problemFields(body: unknown): Partial<ApiProblemInit> {
  if (!isRecord(body)) return {}
  const fields: Partial<ApiProblemInit> = {}
  if (typeof body.title === "string" && body.title) fields.title = body.title
  if (typeof body.type === "string" && body.type) fields.type = body.type
  // A non-problem JSON error (FastAPI's `{"detail": "..."}`) still yields its message.
  if (typeof body.detail === "string") fields.detail = body.detail
  if (Array.isArray(body.errors)) fields.errors = body.errors.filter(isFieldError)
  return fields
}

function isFieldError(value: unknown): value is FieldError {
  return (
    isRecord(value) &&
    typeof value.msg === "string" &&
    Array.isArray(value.loc) &&
    value.loc.every((part) => typeof part === "string" || typeof part === "number")
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

/** A short, user-facing description of any error (for toasts and error states). */
export function errorMessage(error: unknown): { title: string; description?: string } {
  if (isApiProblem(error)) {
    return error.detail ? { title: error.title, description: error.detail } : { title: error.title }
  }
  return { title: "Something went wrong", description: error instanceof Error ? error.message : undefined }
}
