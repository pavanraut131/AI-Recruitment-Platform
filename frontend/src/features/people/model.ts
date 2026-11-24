import type { Schema } from "@/lib/api/client"

/**
 * The Person profile's vocabulary (spec §5.4): API types, contacts and
 * identity conflicts. The profile's free-form entries (work experience,
 * education, projects) are read defensively in `@/lib/profile`.
 */

export type Person = Schema<"Person">
export type PersonApplication = Schema<"PersonApplication">
export type ConflictFlag = Schema<"IdentityConflictFlag">

// The profile's free-form entries are read in `@/lib/profile` (the review panel reads them too).
export {
  educationEntries,
  projectEntries,
  workEntries,
  type EducationEntry,
  type ProjectEntry,
  type WorkEntry,
} from "@/lib/profile"

/** The Person's emails and phones, as first written, in API order. */
export function contacts(person: Pick<Person, "identifiers">): { emails: string[]; phones: string[] } {
  const emails = person.identifiers.filter((id) => id.kind === "email").map((id) => id.value)
  const phones = person.identifiers.filter((id) => id.kind === "phone").map((id) => id.value)
  return { emails, phones }
}

/**
 * The application shown by default: `requested` when it is one of the
 * Person's, else the most recent (the API lists them newest first).
 */
export function selectedApplication(
  applications: readonly PersonApplication[],
  requested: string | null,
): PersonApplication | undefined {
  return applications.find((application) => application.id === requested) ?? applications[0]
}

/** An unresolved identity conflict as a sentence (ADR 0004; resolved in E6). */
export function conflictMessage(conflict: ConflictFlag, applications: readonly PersonApplication[]): string {
  const application = applications.find((item) => item.resume_id === conflict.resume_id)
  const which = application ? `The resume for “${application.campaign.title}”` : "A resume"
  return conflict.role === "chosen"
    ? `${which} shared a phone number with another person. It was attached here because the email matched.`
    : "A resume with this person's phone number was attached to another person, whose email it matched."
}
