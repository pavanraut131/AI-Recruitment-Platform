import type { Schema } from "@/lib/api/client"

export type Role = Schema<"Role">

/** A role as a label (the user menu, the agency picker). */
export const ROLE_LABELS: Record<Role, string> = {
  owner: "Owner",
  admin: "Admin",
  recruiter: "Recruiter",
  viewer: "Viewer",
}

/** A role in a sentence ("as a recruiter"), as the invitation email words it. */
export const ROLE_IN_SENTENCE: Record<Role, string> = {
  owner: "an owner",
  admin: "an admin",
  recruiter: "a recruiter",
  viewer: "a viewer",
}

/**
 * Up to two initials of a name, for an avatar ("Priya Shah" → "PS"). A part
 * in brackets ("Sam Lee (she/her)", "Recruiter (Default agency)") is not part
 * of the name.
 */
export function initials(name: string): string {
  const words = name
    .replace(/\(.*?\)/g, " ")
    .split(/\s+/)
    .filter((word) => /\p{L}/u.test(word))
  const letters = words.length > 1 ? [words[0], words.at(-1)!] : words.slice(0, 1)
  return letters.map((word) => [...word][0].toUpperCase()).join("") || "?"
}

/** The first word of a display name, for a greeting; `null` when there is none. */
export function firstName(name: string): string | null {
  const first = name.trim().split(/\s+/)[0]
  return first ? first : null
}
