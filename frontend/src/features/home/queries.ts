"use client"

import { queryOptions, useQuery } from "@tanstack/react-query"

import { api, unwrap, type Schema } from "@/lib/api/client"
import { queryKeys } from "@/lib/api/query-keys"
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

export function homeQuery() {
  return queryOptions({
    queryKey: queryKeys.home.all,
    queryFn: ({ signal }) => api.GET("/home", { signal }).then(unwrap),
    refetchInterval: (query) => ((query.state.data?.processing.total ?? 0) > 0 ? HOME_POLL_MS : false),
  })
}

export function useHome() {
  return useQuery(homeQuery())
}

/**
 * Workspace-wide counts (`GET /stats`): Home's summary sentence ("12
 * candidates are waiting for a first look") and, when there is nothing to
 * show, whether this is a first run (no campaigns at all) or every campaign
 * is closed. The sidebar reads the same query.
 */
export function useWorkspaceStats() {
  return useQuery(statsQueryOptions())
}
