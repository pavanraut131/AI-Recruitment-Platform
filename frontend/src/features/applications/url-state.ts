import {
  parseAsArrayOf,
  parseAsInteger,
  parseAsNumberLiteral,
  parseAsString,
  parseAsStringLiteral,
  useQueryStates,
  type Values,
} from "nuqs"
import { useCallback, useMemo } from "react"

import type { ApplicationListParams } from "@/lib/api/query-keys"

import {
  CATEGORY_META,
  DEFAULT_PAGE_SIZE,
  DEFAULT_SORT,
  PAGE_SIZES,
  SORTS,
  STAGES,
  type ApplicationSort,
  type Category,
  type CategoryTone,
} from "./model"

/**
 * The candidates table's URL state (ADR 0007: filters, sort, page and the open
 * panel live in the URL, so a view survives a reload and can be shared).
 *
 * Keys are short and readable, e.g.
 * `?q=ada&cat=strong,moderate&min=60&skill=react&stage=screened&sort=-updated_at&page=2&open=<id>`.
 * Defaults are left out of the URL. `tab` is reserved by the campaign workspace.
 *
 * Categories use their tone in the URL (`strong`, not `strong_match`).
 */
const CATEGORY_SLUGS = ["strong", "moderate", "weak", "rejected"] as const satisfies readonly CategoryTone[]

const SLUG_TO_CATEGORY = Object.fromEntries(
  Object.entries(CATEGORY_META).map(([category, meta]) => [meta.tone, category]),
) as Record<CategoryTone, Category>

export function categoryToSlug(category: Category): CategoryTone {
  return CATEGORY_META[category].tone
}

export function slugToCategory(slug: CategoryTone): Category {
  return SLUG_TO_CATEGORY[slug]
}

export const candidatesSearchParams = {
  /** Search in name or email. */
  q: parseAsString.withDefault(""),
  /** Categories (any of). */
  cat: parseAsArrayOf(parseAsStringLiteral(CATEGORY_SLUGS)).withDefault([]),
  /** Score range, 0–100. */
  min: parseAsInteger,
  max: parseAsInteger,
  /** A skill contained in one of the candidate's skills. */
  skill: parseAsString.withDefault(""),
  /** Pipeline stages (any of). */
  stage: parseAsArrayOf(parseAsStringLiteral(STAGES)).withDefault([]),
  sort: parseAsStringLiteral(SORTS).withDefault(DEFAULT_SORT as (typeof SORTS)[number]),
  page: parseAsInteger.withDefault(1),
  size: parseAsNumberLiteral(PAGE_SIZES).withDefault(DEFAULT_PAGE_SIZE),
  /** The application whose panel is open. */
  open: parseAsString,
}

export type CandidatesUrlState = Values<typeof candidatesSearchParams>

/** The keys that filter the list (cleared by "Clear filters"). */
export const FILTER_KEYS = ["q", "cat", "min", "max", "skill", "stage"] as const
export type FilterKey = (typeof FILTER_KEYS)[number]
export type FilterPatch = Partial<Pick<CandidatesUrlState, FilterKey>>

/** A score bound inside 0–100, or `undefined` (no bound) for a missing or invalid one. */
function scoreBound(value: number | null): number | undefined {
  if (value === null || !Number.isFinite(value)) return undefined
  return Math.min(100, Math.max(0, value))
}

/** The filters (no sort or page) as the API's query parameters; empty ones are left out. */
export function toFilterParams(state: Pick<CandidatesUrlState, FilterKey>): ApplicationListParams {
  const params: ApplicationListParams = {}
  const q = state.q.trim()
  if (q) params.q = q.slice(0, 200)
  if (state.cat.length) params.category = [...new Set(state.cat)].map(slugToCategory)
  if (state.stage.length) params.stage = [...new Set(state.stage)]
  const min = scoreBound(state.min)
  const max = scoreBound(state.max)
  if (min !== undefined) params.min_score = min
  if (max !== undefined) params.max_score = max
  const skill = state.skill.trim()
  if (skill) params.skill = skill.slice(0, 100)
  return params
}

/** The URL state as the list request's query parameters (also its query key). */
export function toListParams(state: CandidatesUrlState): ApplicationListParams {
  return {
    ...toFilterParams(state),
    sort: state.sort,
    page: Math.max(1, state.page),
    page_size: state.size,
  }
}

export function hasActiveFilters(state: Pick<CandidatesUrlState, FilterKey>): boolean {
  return Object.keys(toFilterParams(state)).length > 0
}

/** The sortable columns and the server sorts they toggle between. */
export type SortableColumn = "name" | "score" | "updated"

/**
 * The server sort after clicking a column header.
 *
 * - score and updated start descending and then flip (`-score` ↔ `score`);
 * - name sorts A→Z only (the API has no descending name sort), so a second
 *   click returns to the default sort.
 */
export function nextSort(column: SortableColumn, current: ApplicationSort): ApplicationSort {
  switch (column) {
    case "score":
      return current === "-score" ? "score" : "-score"
    case "updated":
      return current === "-updated_at" ? "updated_at" : "-updated_at"
    case "name":
      return current === "name" ? DEFAULT_SORT : "name"
  }
}

/** The column a server sort orders by, and its direction (TanStack's sorting state). */
export function sortToSorting(sort: ApplicationSort): { id: SortableColumn; desc: boolean } {
  switch (sort) {
    case "-score":
      return { id: "score", desc: true }
    case "score":
      return { id: "score", desc: false }
    case "-updated_at":
      return { id: "updated", desc: true }
    case "updated_at":
      return { id: "updated", desc: false }
    case "name":
      return { id: "name", desc: false }
  }
}

const CLEARED_FILTERS = { q: null, cat: null, min: null, max: null, skill: null, stage: null } as const

/**
 * The table's URL state and its setters. Changing a filter, the sort or the
 * page size goes back to page 1. History entries are replaced, not pushed, so
 * typing in the search box does not fill the back button.
 */
export function useCandidatesUrlState() {
  const [state, setState] = useQueryStates(candidatesSearchParams, { history: "replace" })

  const setFilters = useCallback(
    (patch: FilterPatch) => {
      const next: Record<string, unknown> = { page: null }
      for (const [key, value] of Object.entries(patch)) {
        // Empty values leave the URL (instead of `?q=`).
        next[key] = value === "" || (Array.isArray(value) && value.length === 0) ? null : value
      }
      return setState(next as Parameters<typeof setState>[0])
    },
    [setState],
  )
  const clearFilters = useCallback(() => setState({ ...CLEARED_FILTERS, page: null }), [setState])
  const setSort = useCallback((sort: ApplicationSort) => setState({ sort, page: null }), [setState])
  const setPage = useCallback((page: number) => setState({ page: Math.max(1, page) }), [setState])
  const setPageSize = useCallback(
    (size: (typeof PAGE_SIZES)[number]) => setState({ size, page: null }),
    [setState],
  )
  const setOpen = useCallback((open: string | null) => setState({ open }), [setState])

  const listParams = useMemo(() => toListParams(state), [state])

  return { state, listParams, setFilters, clearFilters, setSort, setPage, setPageSize, setOpen }
}
