import { queryOptions } from "@tanstack/react-query"

import { api, unwrap } from "@/lib/api/client"
import { queryKeys } from "@/lib/api/query-keys"

/**
 * Workspace-wide counts (`GET /stats`): campaigns by status, resumes,
 * applications by category and stage, people. Home's summary sentence and
 * the sidebar's counts read it; mutations that change a count invalidate
 * `queryKeys.stats.all`.
 */
export function statsQueryOptions() {
  return queryOptions({
    queryKey: queryKeys.stats.all,
    queryFn: ({ signal }) => api.GET("/stats", { signal }).then(unwrap),
  })
}
