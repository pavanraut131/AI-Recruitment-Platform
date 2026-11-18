import type { Schema } from "@/lib/api/client"
import { SCORE_BAR_CLASS, displayName, type Application, type Stage } from "@/lib/applications"

/**
 * The candidates table's vocabulary. The application's own (types, labels,
 * category tones, formatting) is shared with the Person profile and lives in
 * `@/lib/applications`; this adds what only the table uses (sorts, page sizes).
 */
export {
  CATEGORIES,
  CATEGORY_META,
  SCORE_BAR_CLASS,
  STAGES,
  STAGE_LABELS,
  UNREVIEWED_LABEL,
  formatScore,
  formatYears,
  stageLabel,
  type Application,
  type Category,
  type CategoryTone,
  type Stage,
} from "@/lib/applications"

export type ApplicationPage = Schema<"ApplicationPage">
export type ApplicationSort = Schema<"ApplicationSort">
export type BulkResult = Schema<"BulkApplicationResult">

/** The stage a "Reject" bulk action moves applications to (spec §6.1). */
export const REJECTED_STAGE: Stage = "rejected_manual"

export const SORTS = ["-score", "score", "-updated_at", "updated_at", "name"] as const satisfies readonly ApplicationSort[]
export const DEFAULT_SORT: ApplicationSort = "-score"

/** The API caps `page_size` at 100 (spec §6.1). */
export const PAGE_SIZES = [25, 50, 100] as const
export type PageSize = (typeof PAGE_SIZES)[number]
export const DEFAULT_PAGE_SIZE: PageSize = 50

/** Small dot in a category's colour (filters, legends). */
export const CATEGORY_DOT_CLASS = SCORE_BAR_CLASS

export function personName(application: Application): string {
  return displayName(application.person.name)
}
