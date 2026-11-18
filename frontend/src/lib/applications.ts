import type { Schema } from "@/lib/api/client"

/**
 * The vocabulary of an application, shared by every screen that shows one
 * (the candidates table and review panel in `features/applications`, the
 * Person profile in `features/people`): API types, enum labels, the colour
 * tone of each category (EPIC-01 §3: category tokens in src/styles/tokens.css)
 * and the AI's score breakdown.
 */

export type Application = Schema<"Application">
export type Category = Schema<"ApplicationCategory">
export type Stage = Schema<"PipelineStage">

/** Category tone: the suffix of the `--category-*` tokens and the Badge variant. */
export type CategoryTone = "strong" | "moderate" | "weak" | "rejected"

export const CATEGORY_META = {
  strong_match: { label: "Strong match", tone: "strong" },
  moderate_match: { label: "Moderate match", tone: "moderate" },
  weak_match: { label: "Weak match", tone: "weak" },
  rejected: { label: "Rejected", tone: "rejected" },
} as const satisfies Record<Category, { label: string; tone: CategoryTone }>

/** Display order (best first). */
export const CATEGORIES = Object.keys(CATEGORY_META) as Category[]

export const STAGE_LABELS = {
  screened: "Screened",
  phone_call: "Phone call",
  technical: "Technical",
  offer: "Offer",
  hired: "Hired",
  rejected_manual: "Rejected",
} as const satisfies Record<Stage, string>

/** Pipeline order. */
export const STAGES = Object.keys(STAGE_LABELS) as Stage[]

/** The label of an application that has no stage yet. */
export const UNREVIEWED_LABEL = "Unreviewed"

/** Bar colour of a category's score (solid token). */
export const SCORE_BAR_CLASS = {
  strong: "bg-category-strong",
  moderate: "bg-category-moderate",
  weak: "bg-category-weak",
  rejected: "bg-category-rejected",
} as const satisfies Record<CategoryTone, string>

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
  { key: "skills_match", label: "Skills match", max: 40 },
  { key: "experience_match", label: "Experience match", max: 30 },
  { key: "project_relevance", label: "Project relevance", max: 15 },
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
