import type { FieldValues, Path, UseFormSetError } from "react-hook-form"

import { isApiProblem } from "@/lib/api/problem"
import { waitInWords } from "@/lib/auth/wait"

/*
 * Failed requests in the settings forms and dialogs (E2-10): what to say
 * above the buttons (docs/design/README.md, voice: what happened, what to
 * try), and a 422's field errors under their inputs. The API's own details
 * are written for people ("The agency needs at least one active owner"), so
 * a 403 or 409 shows them as they are.
 */

export type ProblemNotice = { title: string; description?: string }

/** A failure, as a form's alert says it. `tooMany` titles a 429 ("Too many invitations today"). */
export function problemNotice(error: unknown, { tooMany = "Too many attempts" }: { tooMany?: string } = {}): ProblemNotice {
  if (!isApiProblem(error)) return { title: "Something went wrong", description: "Try again." }
  if (error.isNetworkError) {
    return { title: "Could not reach HireFlow AI", description: "Check your connection and try again." }
  }
  if (error.status === 429) {
    return {
      title: tooMany,
      description: error.retryAfter !== undefined ? `Try again in ${waitInWords(error.retryAfter)}.` : "Try again later.",
    }
  }
  if (error.isServerError) return { title: "Something went wrong on our side", description: "Try again in a moment." }
  return { title: error.detail ?? error.title }
}

/**
 * Puts a 422's field errors under their inputs. `fields` maps the API's body
 * field names to the form's. Returns `true` when every error found its field,
 * so nothing else needs saying.
 */
export function setFieldErrors<Values extends FieldValues>(
  error: unknown,
  setError: UseFormSetError<Values>,
  fields: Partial<Record<string, Path<Values>>>,
): boolean {
  if (!isApiProblem(error) || error.status !== 422 || error.errors.length === 0) return false
  let focused = false
  let unmapped = false
  for (const [apiField, message] of Object.entries(error.fieldErrors())) {
    const field = fields[apiField.split(".")[0]]
    if (!field) {
      unmapped = true
      continue
    }
    setError(field, { type: "server", message }, { shouldFocus: !focused })
    focused = true
  }
  return !unmapped
}

/** A failure in one sentence, for a toast's description: "Too many invitations today. Try again in 2 hours." */
export function problemSentence(error: unknown, options?: { tooMany?: string }): string {
  const { title, description } = problemNotice(error, options)
  if (!description) return title
  return `${title.replace(/[.\s]+$/, "")}. ${description}`
}
