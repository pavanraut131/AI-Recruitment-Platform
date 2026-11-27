import type { FieldValues, Path, UseFormSetError } from "react-hook-form"

import { isApiProblem } from "@/lib/api/client"
import { CSRF_FAILURE_DETAIL } from "@/lib/api/csrf"
import { waitInWords } from "@/lib/auth/wait"

/** A failed request, as the sign-in pages say it: a short title and what to do next. */
export type AuthError = { title: string; description?: string }

/**
 * What to tell someone whose sign-in request failed (docs/design/README.md,
 * voice: what happened, what to try). The API's own detail is kept where it is
 * written for people ("Invalid email or password"); a 429 gets its wait from
 * `Retry-After`; internals (CSRF) are said plainly.
 */
export function authError(error: unknown): AuthError {
  if (!isApiProblem(error)) return { title: "Something went wrong", description: "Try again." }
  if (error.isNetworkError) {
    return { title: "Could not reach HireFlow AI", description: "Check your connection and try again." }
  }
  if (error.status === 429) {
    return {
      title: "Too many attempts",
      description: error.retryAfter !== undefined ? `Try again in ${waitInWords(error.retryAfter)}.` : "Try again later.",
    }
  }
  if (error.status === 403 && error.detail === CSRF_FAILURE_DETAIL) {
    return { title: "This page has expired", description: "Reload it and try again." }
  }
  if (error.isServerError) return { title: "Something went wrong on our side", description: "Try again in a moment." }
  return { title: error.detail ?? error.title }
}

/** A 403 that is not the CSRF check: the API refused this account or session ("no active agency access"). */
export function isRefused(error: unknown): boolean {
  return isApiProblem(error) && error.status === 403 && error.detail !== CSRF_FAILURE_DETAIL
}

/**
 * Puts a 422's field errors under their inputs. `fields` maps the API's body
 * field names to the form's (`new_password` → `password`). Returns `true`
 * when every error found its field, so nothing else needs saying.
 */
export function showFieldErrors<Values extends FieldValues>(
  error: unknown,
  setError: UseFormSetError<Values>,
  fields: Partial<Record<string, Path<Values>>>,
): boolean {
  if (!isApiProblem(error) || error.status !== 422 || error.errors.length === 0) return false
  let focused = false
  let unmapped = false
  for (const [apiField, message] of Object.entries(error.fieldErrors())) {
    const field = fields[apiField]
    if (!field) {
      unmapped = true
      continue
    }
    setError(field, { type: "server", message }, { shouldFocus: !focused })
    focused = true
  }
  return !unmapped
}
