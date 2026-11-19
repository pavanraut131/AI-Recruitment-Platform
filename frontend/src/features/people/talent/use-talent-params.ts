"use client"

import * as React from "react"
import { debounce, parseAsInteger, parseAsString, useQueryStates } from "nuqs"

import type { PeopleListParams } from "@/lib/api/query-keys"
import { useDebouncedValue } from "@/lib/use-debounced-value"

export const TALENT_PAGE_SIZE = 20
/** Typing in the search box updates the URL, and fetches, at most this often. */
export const SEARCH_DEBOUNCE_MS = 300

/**
 * The Talent list's URL state (ADR 0007): `?q=ada&page=2`. Defaults are left
 * out, so `/talent` is the first page of everyone.
 */
export const talentSearchParams = {
  q: parseAsString.withDefault(""),
  page: parseAsInteger.withDefault(1),
}

export function useTalentParams() {
  const [{ q, page }, setParams] = useQueryStates(talentSearchParams)

  // nuqs returns the typed value at once and debounces only the URL, so the
  // request (keyed by these params) follows the debounced value. A new search
  // also goes back to page 1: the search and its page are debounced together,
  // so the request never fetches page 1 of the old search first. Paging on its
  // own, and clearing the search, apply at once.
  const currentPage = Math.max(1, page)
  const typed = React.useMemo(() => ({ q, page: currentPage }), [q, currentPage])
  const settled = useDebouncedValue(typed, SEARCH_DEBOUNCE_MS)
  const typing = q.trim() !== "" && q !== settled.q
  const search = q.trim() === "" ? "" : settled.q.trim()
  const requestPage = typing ? settled.page : currentPage

  const apiParams = React.useMemo<PeopleListParams>(
    () => ({ ...(search && { q: search }), page: requestPage, page_size: TALENT_PAGE_SIZE }),
    [search, requestPage],
  )

  return {
    q,
    page: currentPage,
    apiParams,
    hasSearch: q.trim() !== "",
    setSearch: (value: string) =>
      void setParams(
        { q: value || null, page: null },
        { limitUrlUpdates: value ? debounce(SEARCH_DEBOUNCE_MS) : undefined },
      ),
    setPage: (value: number) => void setParams({ page: value <= 1 ? null : value }),
    clearSearch: () => void setParams({ q: null, page: null }),
  }
}
