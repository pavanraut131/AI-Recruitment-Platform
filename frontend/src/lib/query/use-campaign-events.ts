"use client"

import { type QueryClient, type QueryKey, useQueryClient } from "@tanstack/react-query"
import { useEffect, useEffectEvent, useState } from "react"

import {
  CAMPAIGN_EVENT,
  campaignEventsUrl,
  parseResumeEvent,
  type ResumeEvent,
  TERMINAL_RESUME_STATUSES,
} from "@/lib/api/campaign-events"
import type { Schema } from "@/lib/api/client"
import { queryKeys } from "@/lib/api/query-keys"

type ResumePage = Schema<"ResumePage">

/** First reconnect delay; doubles per failed attempt up to {@link RECONNECT_MAX_MS}. */
export const RECONNECT_BASE_MS = 1_000
export const RECONNECT_MAX_MS = 30_000
/** Invalidations from a burst of events are batched into one refetch per key. */
export const INVALIDATE_DEBOUNCE_MS = 300

/** Delay before reconnect attempt `attempt` (0-based): exponential, capped, ±20 % jitter. */
export function reconnectDelay(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** attempt)
  return Math.round(base * (0.8 + 0.4 * random()))
}

/**
 * - `idle`: no campaign, or disabled;
 * - `connecting`: the first connection is being opened;
 * - `open`: subscribed (the server sent `connected`);
 * - `reconnecting`: the stream dropped; waiting to retry with backoff.
 */
export type CampaignEventsStatus = "idle" | "connecting" | "open" | "reconnecting"

export type UseCampaignEventsOptions = {
  /** Default `true`. `false` closes the stream (e.g. a closed campaign). */
  enabled?: boolean
  /** Called for every valid `resume_update`, after the cache is updated. */
  onResumeEvent?: (event: ResumeEvent) => void
}

/**
 * Live processing progress of one campaign (spec §7): one `EventSource` on
 * `GET /api/v2/campaigns/{id}/events` that keeps the Query cache current.
 *
 * - Every `resume_update` patches the resume in the campaign's cached resume
 *   lists (`setQueryData`); a resume no list holds yet refetches them.
 * - A resume reaching `done`, `failed` or `quota_exceeded` refetches the
 *   resume lists (the failed step, the retry count, the `application_id`,
 *   which the event does not carry), the campaign (counts, stats), the
 *   campaign list and Home; `done` also the campaign's applications,
 *   workspace stats and people (a new Person or application).
 *   Invalidations are debounced, so 50 resumes finishing together cause one
 *   refetch per query, not 50.
 * - When the stream drops it is closed and reopened with exponential backoff
 *   (1 s doubling to 30 s, jittered); after a reconnect the resume lists and
 *   the campaign are refetched, since events may have been missed.
 * - Unmounting (or another `campaignId`) closes it. React StrictMode's
 *   mount → unmount → mount closes the first connection before opening the
 *   second, so at most one stream is open per hook.
 *
 * Mount it once per open campaign workspace.
 */
export function useCampaignEvents(
  campaignId: string | null | undefined,
  { enabled = true, onResumeEvent }: UseCampaignEventsOptions = {},
): CampaignEventsStatus {
  const queryClient = useQueryClient()
  const active = enabled && campaignId ? campaignId : null
  const [connection, setConnection] = useState<{
    campaignId: string | null
    status: "open" | "reconnecting"
  }>({ campaignId: null, status: "reconnecting" })

  const notify = useEffectEvent((event: ResumeEvent) => onResumeEvent?.(event))

  useEffect(() => {
    if (active === null || typeof EventSource === "undefined") return
    const cache = new CacheUpdater(queryClient, active)
    let source: EventSource | null = null
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let attempt = 0
    let reconnected = false
    let disposed = false

    const connect = () => {
      // The session cookie is on the API's origin: send it (EPIC-02 §9.4).
      const current = new EventSource(campaignEventsUrl(active), { withCredentials: true })
      source = current
      current.addEventListener(CAMPAIGN_EVENT.connected, () => {
        attempt = 0
        if (reconnected) cache.resync()
        setConnection({ campaignId: active, status: "open" })
      })
      current.addEventListener(CAMPAIGN_EVENT.resumeUpdate, (message) => {
        const event = parseResumeEvent((message as MessageEvent<string>).data)
        if (event === null) return
        cache.apply(event)
        notify(event)
      })
      current.onerror = () => {
        // Take over from the browser's own retry (fixed delay, and none at all
        // after an HTTP error): close, then reopen with backoff.
        current.close()
        if (disposed || source !== current) return
        source = null
        reconnected = true
        setConnection({ campaignId: active, status: "reconnecting" })
        retryTimer = setTimeout(connect, reconnectDelay(attempt))
        attempt += 1
      }
    }

    connect()
    return () => {
      disposed = true
      clearTimeout(retryTimer)
      source?.close()
      source = null
      cache.dispose()
    }
  }, [active, queryClient])

  if (active === null) return "idle"
  return connection.campaignId === active ? connection.status : "connecting"
}

/** Applies one campaign's events to the Query cache. */
class CacheUpdater {
  private pending = new Map<string, QueryKey>()
  private timer: ReturnType<typeof setTimeout> | undefined

  constructor(
    private readonly queryClient: QueryClient,
    private readonly campaignId: string,
  ) {}

  apply(event: ResumeEvent): void {
    const listsKey = queryKeys.resumes.campaignLists(this.campaignId)
    let found = false
    this.queryClient.setQueriesData<ResumePage>({ queryKey: listsKey }, (page) => {
      if (!page) return page
      let changed = false
      const items = page.items.map((resume) => {
        if (resume.id !== event.resume_id) return resume
        changed = true
        return {
          ...resume,
          status: event.status,
          error_message: event.error,
          filename: event.filename ?? resume.filename,
        }
      })
      if (!changed) return page
      found = true
      return { ...page, items }
    })
    if (!found) this.invalidate(listsKey)

    if (!TERMINAL_RESUME_STATUSES.has(event.status)) return
    // The event lacks what the Uploads tab shows for a finished resume: the
    // failed `processing_step`, `retry_count`, and once done its `application_id`.
    this.invalidate(listsKey)
    this.invalidate(queryKeys.campaigns.detail(this.campaignId))
    this.invalidate(queryKeys.campaigns.lists())
    this.invalidate(queryKeys.home.all)
    if (event.status !== "done") return
    this.invalidate(queryKeys.applications.campaignLists(this.campaignId))
    this.invalidate(queryKeys.stats.all)
    this.invalidate(queryKeys.people.all)
  }

  /** After a reconnect: refetch what missed events may have changed. */
  resync(): void {
    this.invalidate(queryKeys.resumes.campaignLists(this.campaignId))
    this.invalidate(queryKeys.campaigns.detail(this.campaignId))
    this.invalidate(queryKeys.applications.campaignLists(this.campaignId))
  }

  /** Flush what is pending (the component is going away; the cache stays). */
  dispose(): void {
    clearTimeout(this.timer)
    this.flush()
  }

  private invalidate(queryKey: QueryKey): void {
    this.pending.set(JSON.stringify(queryKey), queryKey)
    this.timer ??= setTimeout(() => this.flush(), INVALIDATE_DEBOUNCE_MS)
  }

  private flush(): void {
    this.timer = undefined
    const keys = [...this.pending.values()]
    this.pending.clear()
    for (const queryKey of keys) void this.queryClient.invalidateQueries({ queryKey })
  }
}
