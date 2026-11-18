import type { FieldValues, Path, UseFormSetError } from "react-hook-form"

import { errorMessage, isApiProblem } from "@/lib/api/client"

/** The form-level error key (react-hook-form's `root.*` errors are never sent or validated). */
export const SERVER_ERROR = "root.server" as const

/**
 * Puts a failed submit on the form: each 422 field error under its input
 * (`fieldErrors()` gives react-hook-form paths, e.g. `title` or
 * `required_skills.2`, which goes on the skills field), and anything else
 * (other statuses, fields the form does not have, a whole-body error) as one
 * form-level message.
 */
export function applyServerErrors<Values extends FieldValues>(
  error: unknown,
  setError: UseFormSetError<Values>,
  fields: readonly Path<Values>[],
): void {
  const unmapped: string[] = []
  if (isApiProblem(error) && error.status === 422 && error.errors.length > 0) {
    let focused = false
    for (const [path, message] of Object.entries(error.fieldErrors())) {
      const [head, index] = path.split(".")
      const field = fields.find((name) => name === head)
      if (!field) {
        unmapped.push(path ? `${path}: ${message}` : message)
        continue
      }
      const text = index !== undefined && /^\d+$/.test(index) ? `Skill ${Number(index) + 1}: ${message}` : message
      setError(field, { type: "server", message: text }, { shouldFocus: !focused })
      focused = true
    }
    if (unmapped.length === 0) return
  }
  const { title, description } = errorMessage(error)
  const detail = unmapped.length > 0 ? unmapped.join(" · ") : description
  setError(SERVER_ERROR as Path<Values>, { type: "server", message: detail ? `${title}: ${detail}` : title })
}
