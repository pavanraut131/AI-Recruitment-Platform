"use client"

import * as React from "react"
import { debounce, parseAsArrayOf, parseAsInteger, parseAsString, parseAsStringLiteral, useQueryStates } from "nuqs"

import type { CampaignListParams } from "@/lib/api/query-keys"
import { clientIdParam, useClientFilter } from "@/lib/client-filter"
import { useDebouncedValue } from "@/lib/use-debounced-value"

import { CAMPAIGN_STATUSES, type CampaignStatus } from "../schema"

export const CAMPAIGN_LIST_PAGE_SIZE = 20
/** Typing in the search box updates the URL, and fetches, at most this often. */
export const SEARCH_DEBOUNCE_MS = 300

/**
 * The campaign list's URL state (ADR 0007): `?status=active,closed&q=eng&page=2`,
 * and the client (`?client=`, shared with the shell's switcher and Home:
 * `useClientFilter`, which also remembers it). Defaults are left out of the
 * URL, so `/campaigns` is the unfiltered first page (of the remembered client).
 */
export const campaignListSearchParams = {
  status: parseAsArrayOf(parseAsStringLiteral(CAMPAIGN_STATUSES)).withDefault([]),
  q: parseAsString.withDefault(""),
  page: parseAsInteger.withDefault(1),
}

export function useCampaignListParams() {
  const [{ status, q, page }, setParams] = useQueryStates(campaignListSearchParams)
  const { clientId, setClientId } = useClientFilter({ fromUrl: true })

  // nuqs returns the typed value at once and debounces only the URL, so the
  // request (keyed by these params) follows the debounced value.
  const debouncedQ = useDebouncedValue(q, SEARCH_DEBOUNCE_MS)

  // Clearing the search applies at once.
  const search = q.trim() === "" ? "" : debouncedQ.trim()

  const apiParams = React.useMemo<CampaignListParams>(() => {
    return {
      ...(status.length > 0 && { status: CAMPAIGN_STATUSES.filter((item) => status.includes(item)) }),
      ...(search && { q: search }),
      ...clientIdParam(clientId),
      page: Math.max(1, page),
      page_size: CAMPAIGN_LIST_PAGE_SIZE,
    }
  }, [status, search, clientId, page])

  return {
    status,
    q,
    clientId,
    page: Math.max(1, page),
    apiParams,
    /** Filters "Clear filters" clears; the client is the switcher's choice and stays. */
    hasFilters: status.length > 0 || q.trim() !== "",
    /** A new client starts from the first page (`useClientFilter` clears `page`). */
    setClient: setClientId,
    setSearch: (value: string) =>
      void setParams(
        { q: value || null, page: null },
        { limitUrlUpdates: value ? debounce(SEARCH_DEBOUNCE_MS) : undefined },
      ),
    toggleStatus: (value: CampaignStatus) =>
      void setParams({
        // In the enum's order, so the same filter is always the same URL and query key.
        status: CAMPAIGN_STATUSES.filter((item) => (item === value ? !status.includes(item) : status.includes(item))),
        page: null,
      }),
    setPage: (value: number) => void setParams({ page: value <= 1 ? null : value }),
    clearFilters: () => void setParams({ status: null, q: null, page: null }),
  }
}
