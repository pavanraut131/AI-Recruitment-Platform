import type { Schema } from "@/lib/api/client"

/**
 * The candidates table's vocabulary: API types, enum labels and the colour
 * tone of each category (EPIC-01 §3: category tokens in src/styles/tokens.css).
 */

export type Application = Schema<"Application">
export type ApplicationPage = Schema<"ApplicationPage">
export type Category = Schema<"ApplicationCategory">
export type Stage = Schema<"PipelineStage">
export type ApplicationSort = Schema<"ApplicationSort">
export type BulkResult = Schema<"BulkApplicationResult">

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

/** The stage a "Reject" bulk action moves applications to (spec §6.1). */
export const REJECTED_STAGE: Stage = "rejected_manual"

/** The label of an application that has no stage yet. */
export const UNREVIEWED_LABEL = "Unreviewed"

export const SORTS = ["-score", "score", "-updated_at", "updated_at", "name"] as const satisfies readonly ApplicationSort[]
export const DEFAULT_SORT: ApplicationSort = "-score"

/** The API caps `page_size` at 100 (spec §6.1). */
export const PAGE_SIZES = [25, 50, 100] as const
export type PageSize = (typeof PAGE_SIZES)[number]
export const DEFAULT_PAGE_SIZE: PageSize = 50

/** Bar colour of a category's score (solid token). */
export const SCORE_BAR_CLASS = {
  strong: "bg-category-strong",
  moderate: "bg-category-moderate",
  weak: "bg-category-weak",
  rejected: "bg-category-rejected",
} as const satisfies Record<CategoryTone, string>

/** Small dot in a category's colour (filters, legends). */
export const CATEGORY_DOT_CLASS = SCORE_BAR_CLASS

export function personName(application: Application): string {
  return application.person.name?.trim() || "Unnamed candidate"
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
