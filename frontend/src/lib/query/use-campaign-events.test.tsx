import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, renderHook } from "@testing-library/react"
import { StrictMode } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { campaignEventsUrl, parseResumeEvent, type ResumeEvent } from "@/lib/api/campaign-events"
import type { Schema } from "@/lib/api/client"
import { queryKeys } from "@/lib/api/query-keys"

import {
  INVALIDATE_DEBOUNCE_MS,
  RECONNECT_MAX_MS,
  reconnectDelay,
  useCampaignEvents,
  type UseCampaignEventsOptions,
} from "./use-campaign-events"

/** A stand-in for the browser's EventSource that the test drives by hand. */
class MockEventSource {
  static instances: MockEventSource[] = []
  readonly url: string
  closed = false
  onerror: ((event: Event) => void) | null = null
  private listeners = new Map<string, Set<(event: MessageEvent<string>) => void>>()

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  static open(): MockEventSource[] {
    return MockEventSource.instances.filter((source) => !source.closed)
  }

  static latest(): MockEventSource {
    const source = MockEventSource.instances.at(-1)
    if (!source) throw new Error("no EventSource was created")
    return source
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set())
    this.listeners.get(type)!.add(listener)
  }

  removeEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    this.listeners.get(type)?.delete(listener)
  }

  close() {
    this.closed = true
  }

  /** The server sends an event (ignored once closed, like a real stream). */
  emit(type: string, data: unknown) {
    if (this.closed) return
    const event = new MessageEvent(type, { data: typeof data === "string" ? data : JSON.stringify(data) })
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }

  /** The connection drops. */
  fail() {
    this.onerror?.(new Event("error"))
  }
}

const CAMPAIGN = "11111111-1111-1111-1111-111111111111"
const OTHER = "22222222-2222-2222-2222-222222222222"

function resume(id: string, status: Schema<"ResumeStatus"> = "pending"): Schema<"Resume"> {
  return {
    id,
    campaign_id: CAMPAIGN,
    filename: `${id}.pdf`,
    size_bytes: 1000,
    status,
    processing_step: null,
    error_message: null,
    retry_count: 0,
    application_id: null,
    uploaded_at: "2026-11-17T09:00:00Z",
    updated_at: "2026-11-17T09:00:00Z",
  }
}

function resumePage(...items: Schema<"Resume">[]): Schema<"ResumePage"> {
  return { items, total: items.length, page: 1, page_size: 20 }
}

function update(resumeId: string, status: Schema<"ResumeStatus">, extra: Partial<ResumeEvent> = {}): ResumeEvent {
  return {
    resume_id: resumeId,
    status,
    filename: null,
    error: null,
    candidate_name: null,
    score: null,
    category: null,
    person_id: null,
    matched_existing: null,
    ...extra,
  }
}

let queryClient: QueryClient

function renderEvents(
  campaignId: string | null = CAMPAIGN,
  options: UseCampaignEventsOptions = {},
  { strict = false } = {},
) {
  return renderHook(({ id, opts }) => useCampaignEvents(id, opts), {
    initialProps: { id: campaignId, opts: options },
    wrapper: ({ children }) => {
      const tree = <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      return strict ? <StrictMode>{tree}</StrictMode> : tree
    },
  })
}

function isInvalidated(queryKey: readonly unknown[]): boolean | undefined {
  return queryClient.getQueryState(queryKey)?.isInvalidated
}

beforeEach(() => {
  vi.useFakeTimers()
  MockEventSource.instances = []
  vi.stubGlobal("EventSource", MockEventSource)
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

afterEach(() => {
  queryClient.clear()
  vi.useRealTimers()
})

describe("useCampaignEvents", () => {
  it("opens one EventSource on the campaign's v2 events URL and reports when it is subscribed", () => {
    const { result } = renderEvents()

    expect(MockEventSource.instances).toHaveLength(1)
    expect(MockEventSource.latest().url).toBe(campaignEventsUrl(CAMPAIGN))
    expect(MockEventSource.latest().url).toMatch(/\/api\/v2\/campaigns\/11111111-[\w-]+\/events$/)
    expect(result.current).toBe("connecting")

    act(() => MockEventSource.latest().emit("connected", { campaign_id: CAMPAIGN }))
    expect(result.current).toBe("open")
  })

  it("keeps exactly one connection open under React StrictMode", () => {
    renderEvents(CAMPAIGN, {}, { strict: true })

    // StrictMode mounts, unmounts and mounts again: the first stream is closed.
    expect(MockEventSource.open()).toHaveLength(1)
  })

  it("does not connect without a campaign or when disabled", () => {
    const { result } = renderEvents(null)
    expect(result.current).toBe("idle")
    renderEvents(CAMPAIGN, { enabled: false })
    expect(MockEventSource.instances).toHaveLength(0)
  })

  it("patches the resume in every cached resume list of the campaign", () => {
    const firstPage = queryKeys.resumes.list(CAMPAIGN)
    const failedOnly = queryKeys.resumes.list(CAMPAIGN, { status: ["failed"] })
    const otherCampaign = queryKeys.resumes.list(OTHER)
    queryClient.setQueryData(firstPage, resumePage(resume("r-1"), resume("r-2")))
    queryClient.setQueryData(failedOnly, resumePage(resume("r-1", "failed")))
    queryClient.setQueryData(otherCampaign, resumePage(resume("r-1")))
    const onResumeEvent = vi.fn()
    renderEvents(CAMPAIGN, { onResumeEvent })

    const event = update("r-1", "extracting", { filename: "ada.pdf" })
    act(() => MockEventSource.latest().emit("resume_update", event))

    const page = queryClient.getQueryData<Schema<"ResumePage">>(firstPage)!
    expect(page.items[0]).toMatchObject({ id: "r-1", status: "extracting", filename: "ada.pdf", error_message: null })
    expect(page.items[1]).toEqual(resume("r-2"))
    expect(queryClient.getQueryData<Schema<"ResumePage">>(failedOnly)!.items[0].status).toBe("extracting")
    expect(queryClient.getQueryData<Schema<"ResumePage">>(otherCampaign)!.items[0].status).toBe("pending")
    expect(onResumeEvent).toHaveBeenCalledWith(event)

    // A non-terminal update the cache could apply invalidates nothing.
    act(() => vi.advanceTimersByTime(INVALIDATE_DEBOUNCE_MS))
    expect(isInvalidated(firstPage)).toBe(false)
  })

  it("records a failure's error message", () => {
    const key = queryKeys.resumes.list(CAMPAIGN)
    queryClient.setQueryData(key, resumePage(resume("r-1", "processing")))
    renderEvents()

    act(() => MockEventSource.latest().emit("resume_update", update("r-1", "failed", { error: "Not a resume" })))

    expect(queryClient.getQueryData<Schema<"ResumePage">>(key)!.items[0]).toMatchObject({
      status: "failed",
      error_message: "Not a resume",
    })
  })

  it("refetches the resume lists for a resume none of them holds yet", () => {
    const key = queryKeys.resumes.list(CAMPAIGN)
    queryClient.setQueryData(key, resumePage(resume("r-1")))
    renderEvents()

    act(() => MockEventSource.latest().emit("resume_update", update("r-new", "pending")))
    expect(isInvalidated(key)).toBe(false) // debounced
    act(() => vi.advanceTimersByTime(INVALIDATE_DEBOUNCE_MS))
    expect(isInvalidated(key)).toBe(true)
  })

  it("on done, invalidates the campaign's applications, the campaign and the stats, batched", () => {
    const keys = {
      resumes: queryKeys.resumes.list(CAMPAIGN),
      applications: queryKeys.applications.list(CAMPAIGN, { sort: "-score" }),
      otherApplications: queryKeys.applications.list(OTHER),
      campaign: queryKeys.campaigns.detail(CAMPAIGN),
      campaignStats: queryKeys.campaigns.stats(CAMPAIGN),
      campaignList: queryKeys.campaigns.list(),
      stats: queryKeys.stats.all,
      home: queryKeys.home.all,
      people: queryKeys.people.list(),
    }
    queryClient.setQueryData(keys.resumes, resumePage(resume("r-1", "processing"), resume("r-2", "processing")))
    for (const key of Object.values(keys).slice(1)) queryClient.setQueryData(key, {})
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")
    renderEvents()

    act(() => {
      MockEventSource.latest().emit("resume_update", update("r-1", "done", { score: 91, category: "strong_match" }))
      MockEventSource.latest().emit("resume_update", update("r-2", "done", { score: 40, category: "weak_match" }))
    })
    expect(invalidate).not.toHaveBeenCalled()
    act(() => vi.advanceTimersByTime(INVALIDATE_DEBOUNCE_MS))

    for (const [name, key] of Object.entries(keys)) {
      expect({ name, invalidated: isInvalidated(key) }).toEqual({ name, invalidated: name !== "otherApplications" })
    }
    // Two events, one invalidation per key.
    const invalidatedKeys = invalidate.mock.calls.map(([filters]) => JSON.stringify(filters?.queryKey))
    expect(new Set(invalidatedKeys).size).toBe(invalidatedKeys.length)
  })

  it("on failed, refreshes the campaign's counts but not its applications", () => {
    const applications = queryKeys.applications.list(CAMPAIGN)
    const campaign = queryKeys.campaigns.detail(CAMPAIGN)
    queryClient.setQueryData(applications, {})
    queryClient.setQueryData(campaign, {})
    renderEvents()

    act(() => {
      MockEventSource.latest().emit("resume_update", update("r-1", "failed", { error: "Unreadable PDF" }))
      vi.advanceTimersByTime(INVALIDATE_DEBOUNCE_MS)
    })

    expect(isInvalidated(campaign)).toBe(true)
    expect(isInvalidated(applications)).toBe(false)
  })

  it("skips payloads that are not a ResumeEvent", () => {
    const key = queryKeys.resumes.list(CAMPAIGN)
    queryClient.setQueryData(key, resumePage(resume("r-1")))
    const onResumeEvent = vi.fn()
    renderEvents(CAMPAIGN, { onResumeEvent })

    act(() => {
      MockEventSource.latest().emit("resume_update", "{not json")
      MockEventSource.latest().emit("resume_update", { resume_id: "r-1", status: "exploded" })
      vi.advanceTimersByTime(INVALIDATE_DEBOUNCE_MS)
    })

    expect(onResumeEvent).not.toHaveBeenCalled()
    expect(queryClient.getQueryData<Schema<"ResumePage">>(key)!.items[0].status).toBe("pending")
    expect(isInvalidated(key)).toBe(false)
  })

  it("reconnects with exponential backoff after a drop, then resyncs", () => {
    vi.spyOn(Math, "random").mockReturnValue(0.5) // no jitter
    const resumes = queryKeys.resumes.list(CAMPAIGN)
    queryClient.setQueryData(resumes, resumePage(resume("r-1")))
    const { result } = renderEvents()
    act(() => MockEventSource.latest().emit("connected", { campaign_id: CAMPAIGN }))
    const first = MockEventSource.latest()

    act(() => first.fail())
    expect(first.closed).toBe(true)
    expect(result.current).toBe("reconnecting")
    expect(MockEventSource.instances).toHaveLength(1)

    act(() => vi.advanceTimersByTime(reconnectDelay(0) - 1))
    expect(MockEventSource.instances).toHaveLength(1)
    act(() => vi.advanceTimersByTime(1))
    expect(MockEventSource.instances).toHaveLength(2)

    // The second attempt fails before subscribing: the next wait doubles.
    act(() => MockEventSource.latest().fail())
    act(() => vi.advanceTimersByTime(reconnectDelay(1) - 1))
    expect(MockEventSource.instances).toHaveLength(2)
    act(() => vi.advanceTimersByTime(1))
    expect(MockEventSource.instances).toHaveLength(3)
    expect(MockEventSource.open()).toHaveLength(1)

    act(() => {
      MockEventSource.latest().emit("connected", { campaign_id: CAMPAIGN })
      vi.advanceTimersByTime(INVALIDATE_DEBOUNCE_MS)
    })
    expect(result.current).toBe("open")
    expect(isInvalidated(resumes)).toBe(true) // events may have been missed

    // Subscribed again: the backoff starts over.
    act(() => MockEventSource.latest().fail())
    act(() => vi.advanceTimersByTime(reconnectDelay(0)))
    expect(MockEventSource.instances).toHaveLength(4)
  })

  it("closes on unmount and does not reconnect afterwards", () => {
    const { unmount } = renderEvents()
    act(() => MockEventSource.latest().fail())

    unmount()
    act(() => vi.advanceTimersByTime(RECONNECT_MAX_MS * 2))

    expect(MockEventSource.instances).toHaveLength(1)
    expect(MockEventSource.open()).toHaveLength(0)
  })

  it("closes the old stream and opens a new one when the campaign changes", () => {
    const { rerender } = renderEvents()
    const first = MockEventSource.latest()

    rerender({ id: OTHER, opts: {} })

    expect(first.closed).toBe(true)
    expect(MockEventSource.open()).toHaveLength(1)
    expect(MockEventSource.latest().url).toBe(campaignEventsUrl(OTHER))
  })
})

describe("reconnectDelay", () => {
  it("doubles from 1 s, caps at 30 s and jitters by at most 20 %", () => {
    expect([0, 1, 2, 3, 4, 5, 10].map((attempt) => reconnectDelay(attempt, () => 0.5))).toEqual([
      1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000,
    ])
    expect(reconnectDelay(0, () => 0)).toBe(800)
    expect(reconnectDelay(0, () => 1)).toBe(1_200)
  })
})

describe("parseResumeEvent", () => {
  it("fills absent optional fields with null", () => {
    expect(parseResumeEvent(JSON.stringify({ resume_id: "r-1", status: "pending" }))).toEqual(update("r-1", "pending"))
  })

  it("rejects payloads that break the ResumeEvent contract", () => {
    for (const data of [
      "",
      "null",
      "[]",
      JSON.stringify({ status: "done" }),
      JSON.stringify({ resume_id: "r-1", status: "unknown" }),
      JSON.stringify({ resume_id: "r-1", status: "done", score: "high" }),
      JSON.stringify({ resume_id: "r-1", status: "done", filename: 3 }),
    ]) {
      expect(parseResumeEvent(data)).toBeNull()
    }
  })
})
