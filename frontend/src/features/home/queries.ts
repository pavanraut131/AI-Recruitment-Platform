"use client"

import { queryOptions, useQuery } from "@tanstack/react-query"

import { api, unwrap, type Schema } from "@/lib/api/client"
import { queryKeys } from "@/lib/api/query-keys"

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
 * Workspace-wide counts (`GET /stats`). Home asks only when it has nothing to
 * show, to tell a first run (no campaigns at all) from campaigns that are all
 * closed or archived.
 */
export function useWorkspaceStats({ enabled }: { enabled: boolean }) {
  return useQuery({
    queryKey: queryKeys.stats.all,
    queryFn: ({ signal }) => api.GET("/stats", { signal }).then(unwrap),
    enabled,
  })
}
