import { z } from "zod"

/*
 * The password policy (EPIC-02 §4.2, `identity.passwords.check_policy`):
 * 12 to 128 characters, any characters, no composition rules. The API also
 * refuses a password equal to the email address (a 422 shown on the field).
 * Lengths count characters as the API does (code points), so an emoji is one.
 */
export const PASSWORD_MIN = 12
export const PASSWORD_MAX = 128

export const PASSWORD_HINT = `At least ${PASSWORD_MIN} characters. A few unrelated words make a strong one.`

function length(value: string): number {
  return [...value].length
}

/** A new password (reset, accepting an invitation), checked like the API checks it. */
export const newPasswordSchema = z
  .string()
  .refine((value) => length(value) > 0, "Choose a password")
  .refine((value) => length(value) === 0 || length(value) >= PASSWORD_MIN, `Use at least ${PASSWORD_MIN} characters`)
  .refine((value) => length(value) <= PASSWORD_MAX, `Use at most ${PASSWORD_MAX} characters`)
