import type { Schema } from "@/lib/api/client"

/**
 * The vocabulary of an application, shared by every screen that shows one
 * (the candidates table and review panel in `features/applications`, the
 * Person profile in `features/people`, Home): API types, the words the UI
 * uses for them (docs/design/README.md, voice), the colour tone of each
 * category (category tokens in src/styles/tokens.css) and the score breakdown.
 */

export type Application = Schema<"Application">
export type Category = Schema<"ApplicationCategory">
export type Stage = Schema<"PipelineStage">

/** Category tone: the suffix of the `--category-*` tokens and the Badge variant. */
export type CategoryTone = "strong" | "moderate" | "weak" | "rejected"

/**
 * How a category reads (the API's values do not change, only their words):
 * `label` on its own ("Possible match"), `short` inside a sentence ("3 strong,
 * 9 possible"). Strong and possible are coloured; unlikely and not-a-match
 * are both grey.
 */
export const CATEGORY_META = {
  strong_match: { label: "Strong match", short: "strong", tone: "strong" },
  moderate_match: { label: "Possible match", short: "possible", tone: "moderate" },
  weak_match: { label: "Unlikely match", short: "unlikely", tone: "weak" },
  rejected: { label: "Not a match", short: "not a match", tone: "rejected" },
} as const satisfies Record<Category, { label: string; short: string; tone: CategoryTone }>

/** Display order (best first). */
export const CATEGORIES = Object.keys(CATEGORY_META) as Category[]

/** Pipeline stages in a recruiter's words (`rejected_manual` is the recruiter's "Not a fit"). */
export const STAGE_LABELS = {
  screened: "Screened",
  phone_call: "Phone screen",
  technical: "Technical",
  offer: "Offer",
  hired: "Hired",
  rejected_manual: "Not a fit",
} as const satisfies Record<Stage, string>

/** Pipeline order. */
export const STAGES = Object.keys(STAGE_LABELS) as Stage[]

/** `ApplicationUpdate.notes` is at most this long (the API answers 422 beyond it). */
export const NOTES_MAX_LENGTH = 10_000

/** The label of an application that has no stage yet. */
export const UNREVIEWED_LABEL = "New"

/** The stage "Not a fit" moves an application to. */
export const NOT_A_FIT_STAGE: Stage = "rejected_manual"

/**
 * The review panel's primary action: the next stage in the pipeline, as a
 * verb ("Move to phone screen"). Hired and not-a-fit have none.
 */
const NEXT_STEP: Record<Stage | "new", { stage: Stage; label: string } | null> = {
  new: { stage: "screened", label: "Mark as screened" },
  screened: { stage: "phone_call", label: "Move to phone screen" },
  phone_call: { stage: "technical", label: "Move to technical" },
  technical: { stage: "offer", label: "Move to offer" },
  offer: { stage: "hired", label: "Mark as hired" },
  hired: null,
  rejected_manual: null,
}

export function nextStep(stage: Stage | null): { stage: Stage; label: string } | null {
  return NEXT_STEP[stage ?? "new"]
}

/** Bar colour of a category's score (solid token). */
export const SCORE_BAR_CLASS = {
  strong: "bg-category-strong",
  moderate: "bg-category-moderate",
  weak: "bg-category-weak",
  rejected: "bg-category-rejected",
} as const satisfies Record<CategoryTone, string>

/** Text colour of a category's label (≥ 4.5:1 on the page and on a selected row). */
export const CATEGORY_TEXT_CLASS = {
  strong: "text-category-strong-foreground",
  moderate: "text-category-moderate-foreground",
  weak: "text-category-weak-foreground",
  rejected: "text-category-rejected-foreground",
} as const satisfies Record<CategoryTone, string>

export type MustHave = { skill: string; has: boolean }

/**
 * The campaign's required skills, each checked against the candidate's
 * extracted skills (case-insensitive), in the campaign's order.
 */
export function mustHaves(required: readonly string[], skills: readonly string[]): MustHave[] {
  const own = new Set(skills.map((skill) => skill.trim().toLowerCase()))
  const seen = new Set<string>()
  const result: MustHave[] = []
  for (const raw of required) {
    const skill = raw.trim()
    const key = skill.toLowerCase()
    if (!skill || seen.has(key)) continue
    seen.add(key)
    result.push({ skill, has: own.has(key) })
  }
  return result
}

/** "4 of 5": how many must-haves a candidate has (`null` when the campaign lists none). */
export function mustHaveSummary(required: ReadonlySet<string>, skills: readonly string[]): string | null {
  if (required.size === 0) return null
  const own = new Set(skills.map((skill) => skill.trim().toLowerCase()))
  let count = 0
  for (const skill of required) if (own.has(skill)) count++
  return `${count} of ${required.size}`
}

/** A person's name for display; resumes without one read "Unnamed candidate". */
export function displayName(name: string | null | undefined): string {
  return name?.trim() || "Unnamed candidate"
}

export function formatScore(score: number | null): string {
  return score === null ? "—" : String(Math.round(score))
}

export function formatYears(years: number | null): string {
  if (years === null) return "—"
  const rounded = Math.round(years * 10) / 10
  return `${rounded} ${rounded === 1 ? "yr" : "yrs"}`
}

export function stageLabel(stage: Stage | null): string {
  return stage === null ? UNREVIEWED_LABEL : STAGE_LABELS[stage]
}

/** A date as "12 Nov 2026" (dates in lists and headers). */
export function formatDate(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return "—"
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })
}

/**
 * The AI's sub-scores, in the order and with the maxima of the scoring prompt
 * (backend `app/modules/ai/prompts.py`): they add up to the overall score.
 */
export const SCORE_PARTS = [
  { key: "skills_match", label: "Skills", max: 40 },
  { key: "experience_match", label: "Experience", max: 30 },
  { key: "project_relevance", label: "Relevant projects", max: 15 },
  { key: "education_bonus", label: "Education", max: 10 },
  { key: "communication_quality", label: "Resume quality", max: 5 },
] as const

export type ScorePart = { key: string; label: string; value: number; max: number }

/**
 * The breakdown's known sub-scores as bars, in prompt order. The API types it
 * as an open object (`score_breakdown` is JSON from the AI), so values that
 * are missing or not numbers are left out; `null` means "not scored".
 */
export function scoreParts(breakdown: unknown): ScorePart[] | null {
  if (breakdown === null || typeof breakdown !== "object" || Array.isArray(breakdown)) return null
  const values = breakdown as Record<string, unknown>
  const parts: ScorePart[] = []
  for (const { key, label, max } of SCORE_PARTS) {
    const raw = values[key]
    const value = typeof raw === "number" ? raw : typeof raw === "string" ? Number(raw) : Number.NaN
    if (Number.isFinite(value)) parts.push({ key, label, value: Math.min(max, Math.max(0, value)), max })
  }
  return parts.length > 0 ? parts : null
}
