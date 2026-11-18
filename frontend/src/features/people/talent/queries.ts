"use client"

import { keepPreviousData, queryOptions, useQuery } from "@tanstack/react-query"

import { api, unwrap, type Schema } from "@/lib/api/client"
import { queryKeys, type PeopleListParams } from "@/lib/api/query-keys"

export type PersonListItem = Schema<"PersonListItem">

export function peopleListQuery(params: PeopleListParams) {
  return queryOptions({
    queryKey: queryKeys.people.list(params),
    queryFn: ({ signal }) => api.GET("/people", { params: { query: params }, signal }).then(unwrap),
  })
}

/** One page of people. The previous page stays on screen while the next search or page loads. */
export function usePeople(params: PeopleListParams) {
  return useQuery({ ...peopleListQuery(params), placeholderData: keepPreviousData })
}
