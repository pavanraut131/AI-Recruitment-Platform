import { CATEGORIES, STAGES, type Application, type Category } from "./model"

/**
 * Deterministic synthetic applications, for tests, the 500-row benchmark and
 * the dev route's fixture mode. Not used by product code.
 */

const FIRST = ["Ada", "Grace", "Linus", "Margaret", "Alan", "Barbara", "Dennis", "Frances", "Ken", "Radia"]
const LAST = ["Lovelace", "Hopper", "Torvalds", "Hamilton", "Turing", "Liskov", "Ritchie", "Allen", "Thompson", "Perlman"]
const SKILLS = ["React", "TypeScript", "Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes", "AWS", "GraphQL", "Go", "Rust", "Terraform"]

export const FIXTURE_CAMPAIGN_ID = "00000000-0000-4000-8000-000000000000"

function categoryFor(score: number): Category {
  if (score >= 75) return CATEGORIES[0]
  if (score >= 55) return CATEGORIES[1]
  if (score >= 35) return CATEGORIES[2]
  return CATEGORIES[3]
}

function uuid(index: number): string {
  return `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`
}

/** One application; `overrides` replace any field. */
export function makeApplication(index: number, overrides: Partial<Application> = {}): Application {
  const score = index % 17 === 16 ? null : Math.round(((index * 37) % 1000) / 10)
  const first = FIRST[index % FIRST.length]
  const last = LAST[Math.floor(index / FIRST.length) % LAST.length]
  const skills = Array.from({ length: 2 + (index % 5) }, (_, k) => SKILLS[(index + k * 3) % SKILLS.length])
  const updated = new Date(Date.UTC(2026, 10, 1) + index * 3_600_000).toISOString()
  return {
    id: uuid(index + 1),
    campaign_id: FIXTURE_CAMPAIGN_ID,
    resume_id: uuid(100_000 + index),
    person: { id: uuid(200_000 + index), name: `${first} ${last}`, email: `${first}.${last}${index}@example.com`.toLowerCase() },
    score,
    score_breakdown: null,
    category: score === null ? null : categoryFor(score),
    summary: score === null ? null : `${first} has ${index % 12} years of experience building web products.`,
    strengths: [],
    weaknesses: [],
    missing_skills: [],
    recommendation: null,
    skills,
    years_of_experience: index % 9 === 8 ? null : index % 12,
    pipeline_stage: index % 4 === 0 ? null : STAGES[index % STAGES.length],
    notes: null,
    created_at: updated,
    updated_at: updated,
    ...overrides,
  }
}

/** `count` applications, sorted like the API's default (score descending, unscored last). */
export function makeApplications(count: number): Application[] {
  return Array.from({ length: count }, (_, index) => makeApplication(index)).sort(
    (a, b) => (b.score ?? -1) - (a.score ?? -1),
  )
}
