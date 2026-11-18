import type { Schema } from "@/lib/api/client"

/**
 * The Person profile's vocabulary (spec §5.4): API types and readers for the
 * profile's free-form entries. Work experience, education and projects are
 * JSON from the AI's extraction (the API types them as open objects), so every
 * field is read defensively and missing ones are left out.
 */

export type Person = Schema<"Person">
export type PersonApplication = Schema<"PersonApplication">
export type ConflictFlag = Schema<"IdentityConflictFlag">

export type WorkEntry = { role: string | null; company: string | null; duration: string | null; description: string | null }
export type EducationEntry = { degree: string | null; institution: string | null; year: string | null }
export type ProjectEntry = { name: string | null; description: string | null; technologies: string[] }

function text(value: unknown): string | null {
  if (typeof value === "number" && Number.isFinite(value)) return String(value)
  if (typeof value !== "string") return null
  const trimmed = value.trim()
  return trimmed === "" ? null : trimmed
}

function entries(list: unknown): Record<string, unknown>[] {
  if (!Array.isArray(list)) return []
  return list.filter((item): item is Record<string, unknown> => item !== null && typeof item === "object" && !Array.isArray(item))
}

export function workEntries(list: unknown): WorkEntry[] {
  return entries(list)
    .map((item) => ({
      role: text(item.role) ?? text(item.title),
      company: text(item.company),
      duration: text(item.duration),
      description: text(item.description),
    }))
    .filter((entry) => entry.role || entry.company)
}

export function educationEntries(list: unknown): EducationEntry[] {
  return entries(list)
    .map((item) => ({ degree: text(item.degree), institution: text(item.institution), year: text(item.year) }))
    .filter((entry) => entry.degree || entry.institution)
}

export function projectEntries(list: unknown): ProjectEntry[] {
  return entries(list)
    .map((item) => ({
      name: text(item.name),
      description: text(item.description),
      technologies: Array.isArray(item.technologies)
        ? item.technologies.map(text).filter((value): value is string => value !== null)
        : [],
    }))
    .filter((entry) => entry.name || entry.description)
}

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
