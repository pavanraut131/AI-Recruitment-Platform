"use client"

import { queryOptions, useQuery } from "@tanstack/react-query"

import { api, unwrap, type Schema } from "@/lib/api/client"
import { queryKeys } from "@/lib/api/query-keys"
import { clientIdParam } from "@/lib/client-filter"
import { statsQueryOptions } from "@/lib/query/stats"

export type Home = Schema<"Home">
export type ProcessingResume = Schema<"ProcessingResume">
export type RecentMatch = Schema<"RecentMatch">

/**
 * While resumes are processing, Home refetches this often. There is no
 * event stream across campaigns (each workspace streams its own campaign's
 * events), so this is the one place the app polls, and only while something
 * is in progress (backlog: a workspace-wide stream).
 */
export const HOME_POLL_MS = 5_000

/** `GET /home` for every client the user sees, or for `clientId` alone (E2-10). */
export function homeQuery(clientId: string | null = null) {
  const params = clientIdParam(clientId)
  return queryOptions({
    queryKey: queryKeys.home.detail(params),
    queryFn: ({ signal }) => api.GET("/home", { params: { query: params }, signal }).then(unwrap),
    refetchInterval: (query) => ((query.state.data?.processing.total ?? 0) > 0 ? HOME_POLL_MS : false),
  })
}

export function useHome(clientId: string | null = null) {
  return useQuery(homeQuery(clientId))
}

/**
 * Workspace-wide counts (`GET /stats`): Home's summary sentence ("12
 * candidates are waiting for a first look") and, when there is nothing to
 * show, whether this is a first run (no campaigns at all) or every campaign
 * is closed. The sidebar reads the same query.
 */
export function useWorkspaceStats(clientId: string | null = null) {
  return useQuery(statsQueryOptions(clientIdParam(clientId)))
}
