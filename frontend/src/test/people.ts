import type { Schema } from "@/lib/api/client"

type Person = Schema<"Person">
type PersonApplication = Schema<"PersonApplication">

/** A Person as `GET /people/{id}` returns it; `overrides` replace any field. */
export function makePerson(overrides: Partial<Person> = {}): Person {
  return {
    id: "aaaaaaaa-0000-4000-8000-000000000001",
    name: "Ada Lovelace",
    github_url: null,
    linkedin_url: null,
    profile: {
      skills: ["React", "TypeScript"],
      education: [],
      work_experience: [],
      certifications: [],
      projects: [],
      years_of_experience: 6,
    },
    identifiers: [{ kind: "email", value: "ada@example.com", inferred: false }],
    conflicts: [],
    has_unresolved_conflicts: false,
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-11-01T09:00:00Z",
    applications: [],
    ...overrides,
  }
}

/** One entry of a Person's `applications`. */
export function makePersonApplication(
  id: string,
  campaign: { id: string; title: string; status?: PersonApplication["campaign"]["status"] },
  overrides: Partial<PersonApplication> = {},
): PersonApplication {
  return {
    id,
    campaign: { status: "active", ...campaign },
    resume_id: `resume-${id}`,
    score: 80,
    category: "strong_match",
    pipeline_stage: null,
    created_at: "2026-11-01T09:00:00Z",
    updated_at: "2026-11-01T09:00:00Z",
    ...overrides,
  }
}
