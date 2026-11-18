import { queryOptions } from "@tanstack/react-query"

import { api, unwrap } from "@/lib/api/client"
import { queryKeys } from "@/lib/api/query-keys"

/**
 * One Person with their profile, identity conflicts and every application
 * across campaigns: the Person profile (`features/people`) and the review
 * panel's "Also applied to" (`features/applications`) both read it.
 */
export function personQueryOptions(personId: string) {
  return queryOptions({
    queryKey: queryKeys.people.detail(personId),
    queryFn: ({ signal }) =>
      api.GET("/people/{person_id}", { params: { path: { person_id: personId } }, signal }).then(unwrap),
  })
}
