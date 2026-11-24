/**
 * Readers for a Person's extracted profile (`Person.profile`), shared by the
 * Person profile (`features/people`) and the review panel's "Recent
 * experience" (`features/applications`). Work experience, education and
 * projects are JSON from the AI's extraction (the API types them as open
 * objects), so every field is read defensively and missing ones are left out.
 */

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

/** "Backend lead at Finch Pay": a work entry as one line (role, company, or both). */
export function workLine(entry: WorkEntry): string {
  if (entry.role && entry.company) return `${entry.role} at ${entry.company}`
  return entry.role ?? entry.company ?? ""
}
