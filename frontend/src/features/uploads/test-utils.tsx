import { QueryClientProvider, type QueryClient } from "@tanstack/react-query"
import { render } from "@testing-library/react"
import { NuqsTestingAdapter } from "nuqs/adapters/testing"
import type { ReactElement } from "react"
import { vi } from "vitest"

import type { Schema } from "@/lib/api/client"
import { PROBLEM_MEDIA_TYPE } from "@/lib/api/problem"
import { makeQueryClient } from "@/lib/query/query-client"

/*
 * Test helpers for the uploads feature: a fake `/api/v2` behind `fetch` that
 * keeps the campaign's resumes, and hand-driven stand-ins for the browser's
 * XMLHttpRequest (the upload) and EventSource (the campaign's event stream,
 * as in src/lib/query/use-campaign-events.test.tsx).
 */

export const CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"

type Resume = Schema<"Resume">

export function makeResume(id: string, overrides: Partial<Resume> = {}): Resume {
  return {
    id,
    campaign_id: CAMPAIGN_ID,
    filename: `${id}.pdf`,
    size_bytes: 120_000,
    status: "pending",
    processing_step: null,
    error_message: null,
    retry_count: 0,
    application_id: null,
    uploaded_at: "2026-11-18T09:00:00Z",
    updated_at: "2026-11-18T09:00:00Z",
    ...overrides,
  }
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? PROBLEM_MEDIA_TYPE : "application/json" },
  })
}

export function problem(status: number, title: string, detail?: string): Response {
  return json({ type: "about:blank", title, status, ...(detail ? { detail } : {}) }, status)
}

export type ApiCall = { method: string; path: string; url: URL }

/**
 * A fake `/api/v2` on `fetch`. `server.resumes` is the campaign's resumes,
 * newest first: tests change it to play the backend's part (a worker moving a
 * resume on), and the resume list, the campaign's counts and retry-failed
 * answer from it. `override` answers a call first when it returns a response.
 */
export function installFakeApi({
  resumes = [],
  override,
}: {
  resumes?: Resume[]
  override?: (call: ApiCall) => Response | undefined
} = {}) {
  const server = { resumes: [...resumes] }
  const calls: ApiCall[] = []
  const fetchMock = vi.fn(async (request: Request) => {
    const url = new URL(request.url)
    const path = url.pathname.replace(/^\/api\/v2/, "")
    const call = { method: request.method, path, url }
    calls.push(call)
    const custom = override?.(call)
    if (custom) return custom

    if (request.method === "GET" && /^\/campaigns\/[^/]+\/resumes$/.test(path)) {
      const page = Number(url.searchParams.get("page") ?? 1)
      const size = Number(url.searchParams.get("page_size") ?? 20)
      const items = server.resumes.slice((page - 1) * size, page * size)
      return json({ items, total: server.resumes.length, page, page_size: size })
    }
    if (request.method === "POST" && /^\/campaigns\/[^/]+\/resumes\/retry-failed$/.test(path)) {
      const retried = server.resumes.filter((r) => r.status === "failed" || r.status === "quota_exceeded")
      for (const resume of retried) Object.assign(resume, { status: "pending", error_message: null, retry_count: 0 })
      return json({ retried: retried.map((r) => r.id) })
    }
    if (request.method === "GET" && /^\/campaigns\/[^/]+$/.test(path)) {
      const count = (...statuses: Resume["status"][]) => server.resumes.filter((r) => statuses.includes(r.status)).length
      return json({
        id: CAMPAIGN_ID,
        title: "Senior frontend engineer",
        role: "Frontend engineer",
        description: null,
        required_skills: [],
        status: "active",
        client: { id: "00000000-0000-4000-8000-00000000c001", name: "General" },
        created_at: "2026-11-01T00:00:00Z",
        updated_at: "2026-11-01T00:00:00Z",
        last_activity_at: "2026-11-01T00:00:00Z",
        counts: {
          resumes: server.resumes.length,
          in_progress: count("pending", "extracting", "processing"),
          failed: count("failed", "quota_exceeded"),
          new_resumes_24h: 0,
          applications: count("done"),
          strong_matches: 0,
        },
      } satisfies Schema<"Campaign">)
    }
    return problem(404, "Not Found")
  })
  vi.stubGlobal("fetch", fetchMock)
  const callsTo = (method: string, pattern: RegExp) =>
    calls.filter((call) => call.method === method && pattern.test(call.path))
  return { server, calls, callsTo }
}

type Listener = (event: never) => void

/** A stand-in for XMLHttpRequest that the test answers by hand. */
export class MockXHR {
  static instances: MockXHR[] = []
  method = ""
  url = ""
  body: FormData | null = null
  withCredentials = false
  /** Request headers, by name as set. */
  readonly headers: Record<string, string> = {}
  responseType = ""
  status = 0
  statusText = ""
  responseText = ""
  private contentType: string | null = null
  private listeners = new Map<string, Set<Listener>>()
  private uploadListeners = new Map<string, Set<Listener>>()
  readonly upload = {
    addEventListener: (type: string, listener: Listener) => add(this.uploadListeners, type, listener),
  }

  static install(): void {
    MockXHR.instances = []
    vi.stubGlobal("XMLHttpRequest", MockXHR)
  }

  static latest(): MockXHR {
    const request = MockXHR.instances.at(-1)
    if (!request) throw new Error("no upload request was sent")
    return request
  }

  open(method: string, url: string) {
    this.method = method
    this.url = url
  }

  setRequestHeader(name: string, value: string) {
    this.headers[name] = value
  }

  send(body: FormData) {
    this.body = body
    MockXHR.instances.push(this)
  }

  addEventListener(type: string, listener: Listener) {
    add(this.listeners, type, listener)
  }

  getResponseHeader(name: string): string | null {
    return name.toLowerCase() === "content-type" ? this.contentType : null
  }

  /** The files sent in the multipart field `files`. */
  files(): File[] {
    return (this.body?.getAll("files") ?? []) as File[]
  }

  /** The browser reports bytes sent. */
  progress(loaded: number, total: number) {
    fire(this.uploadListeners, "progress", { lengthComputable: true, loaded, total })
  }

  /** The server answers. */
  respond(status: number, body: unknown, contentType = status >= 400 ? PROBLEM_MEDIA_TYPE : "application/json") {
    this.status = status
    this.statusText = status === 202 ? "Accepted" : ""
    this.responseText = JSON.stringify(body)
    this.contentType = contentType
    fire(this.listeners, "load", {})
  }

  /** The request gets no response (offline, CORS, server down). */
  fail() {
    fire(this.listeners, "error", {})
  }
}

function add(map: Map<string, Set<Listener>>, type: string, listener: Listener) {
  if (!map.has(type)) map.set(type, new Set())
  map.get(type)!.add(listener)
}

function fire(map: Map<string, Set<Listener>>, type: string, event: object) {
  for (const listener of map.get(type) ?? []) (listener as (event: object) => void)(event)
}

/** A stand-in for EventSource that the test drives by hand. */
export class MockEventSource {
  static instances: MockEventSource[] = []
  readonly url: string
  readonly withCredentials: boolean
  closed = false
  onerror: ((event: Event) => void) | null = null
  private listeners = new Map<string, Set<(event: MessageEvent<string>) => void>>()

  constructor(url: string, init?: EventSourceInit) {
    this.url = url
    this.withCredentials = init?.withCredentials ?? false
    MockEventSource.instances.push(this)
  }

  static install(): void {
    MockEventSource.instances = []
    vi.stubGlobal("EventSource", MockEventSource)
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

  emit(type: string, data: unknown) {
    if (this.closed) return
    const event = new MessageEvent(type, { data: typeof data === "string" ? data : JSON.stringify(data) })
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }

  /** A `resume_update` with every field, as the backend sends it. */
  resumeUpdate(resumeId: string, status: Resume["status"], extra: Partial<Schema<"ResumeEvent">> = {}) {
    this.emit("resume_update", {
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
    })
  }
}

/** A PDF (or any file) of `size` bytes, without allocating them. */
export function makeFile(name: string, size = 1_000, type = name.toLowerCase().endsWith(".pdf") ? "application/pdf" : ""): File {
  const file = new File(["%PDF-1.4"], name, { type })
  Object.defineProperty(file, "size", { value: size })
  return file
}

/** The app's QueryClient without query retries (a failed load shows its error at once). */
export function testQueryClient(): QueryClient {
  const client = makeQueryClient()
  client.setDefaultOptions({ ...client.getDefaultOptions(), queries: { ...client.getDefaultOptions().queries, retry: false } })
  return client
}

export function renderWithProviders(ui: ReactElement, { queryClient = testQueryClient() }: { queryClient?: QueryClient } = {}) {
  const view = render(
    <QueryClientProvider client={queryClient}>
      <NuqsTestingAdapter hasMemory>{ui}</NuqsTestingAdapter>
    </QueryClientProvider>,
  )
  return {
    ...view,
    queryClient,
    rerenderWith: (next: ReactElement) =>
      view.rerender(
        <QueryClientProvider client={queryClient}>
          <NuqsTestingAdapter hasMemory>{next}</NuqsTestingAdapter>
        </QueryClientProvider>,
      ),
  }
}
