import { queryOptions } from "@tanstack/react-query"

import { api, unwrap } from "@/lib/api/client"
import { queryKeys, type StatsParams } from "@/lib/api/query-keys"

/**
 * Workspace-wide counts (`GET /stats`): campaigns by status, resumes,
 * applications by category and stage, people. Home's summary sentence and
 * the sidebar's counts read it, for every client or the chosen one
 * (`client_id`, E2-10; the people count is always the whole agency's).
 * Mutations that change a count invalidate `queryKeys.stats.all`.
 */
export function statsQueryOptions(params: StatsParams = {}) {
  return queryOptions({
    queryKey: queryKeys.stats.detail(params),
    queryFn: ({ signal }) => api.GET("/stats", { params: { query: params }, signal }).then(unwrap),
  })
}
