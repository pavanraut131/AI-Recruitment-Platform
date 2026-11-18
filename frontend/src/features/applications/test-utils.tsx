import { QueryClientProvider, type QueryClient } from "@tanstack/react-query"
import { render } from "@testing-library/react"
import { NuqsTestingAdapter, type UrlUpdateEvent } from "nuqs/adapters/testing"
import type { ReactElement } from "react"
import { vi } from "vitest"

import { makeQueryClient } from "@/lib/query/query-client"

import { makeApplication } from "./fixtures"
import type { Application, BulkResult } from "./model"

/**
 * Test helpers for the applications feature: DOM APIs that jsdom lacks (Radix
 * needs them), a fake `/api/v2` behind `fetch`, and a render with Query and
 * nuqs providers.
 */

export function installDomPolyfills(): void {
  if (!("ResizeObserver" in globalThis)) {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    )
  }
  const proto = Element.prototype as unknown as Record<string, unknown>
  proto.scrollIntoView ??= function scrollIntoView() {}
  proto.hasPointerCapture ??= () => false
  proto.setPointerCapture ??= () => {}
  proto.releasePointerCapture ??= () => {}
}

export const CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"

export type ApiCall = { method: string; path: string; url: URL; body: unknown }

type Override = (call: ApiCall) => Response | Promise<Response> | undefined

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  })
}

export function problem(status: number, title: string, detail?: string): Response {
  return json({ type: "about:blank", title, status, ...(detail ? { detail } : {}) }, status)
}

/**
 * A fake `/api/v2` on `fetch`: lists `applications` (paged by `page` and
 * `page_size`; filters are asserted from the recorded calls, not applied),
 * answers the campaign, one application, bulk actions and the CSV export.
 * `override` answers a call first when it returns a response.
 */
export function installFakeApi({
  applications = Array.from({ length: 3 }, (_, index) => makeApplication(index)),
  total,
  requiredSkills = ["React", "PostgreSQL"],
  override,
}: {
  applications?: Application[]
  total?: number
  requiredSkills?: string[]
  override?: Override
} = {}) {
  const calls: ApiCall[] = []
  const fetchMock = vi.fn(async (request: Request) => {
    const url = new URL(request.url)
    const path = url.pathname.replace(/^\/api\/v2/, "")
    const body = request.method === "GET" ? undefined : await request.clone().json().catch(() => undefined)
    const call = { method: request.method, path, url, body }
    calls.push(call)
    const custom = await override?.(call)
    if (custom) return custom

    if (request.method === "GET" && /^\/campaigns\/[^/]+\/applications$/.test(path)) {
      const page = Number(url.searchParams.get("page") ?? 1)
      const size = Number(url.searchParams.get("page_size") ?? 20)
      const items = applications.slice((page - 1) * size, page * size)
      return json({ items, total: total ?? applications.length, page, page_size: size })
    }
    if (request.method === "GET" && /^\/campaigns\/[^/]+\/applications\/export$/.test(path)) {
      return new Response("name,email\n", { headers: { "Content-Type": "text/csv" } })
    }
    if (request.method === "POST" && /^\/campaigns\/[^/]+\/applications\/bulk$/.test(path)) {
      const { action, ids } = body as { action: BulkResult["action"]; ids: string[] }
      const known = new Set(applications.map((application) => application.id))
      return json({
        action,
        affected: ids.filter((id) => known.has(id)),
        not_found: ids.filter((id) => !known.has(id)),
      } satisfies BulkResult)
    }
    if (request.method === "GET" && /^\/campaigns\/[^/]+$/.test(path)) {
      return json({
        id: CAMPAIGN_ID,
        title: "Senior frontend engineer",
        role: "Frontend engineer",
        description: null,
        required_skills: requiredSkills,
        status: "active",
        created_at: "2026-11-01T00:00:00Z",
        updated_at: "2026-11-01T00:00:00Z",
        last_activity_at: "2026-11-01T00:00:00Z",
        counts: { resumes: 0, in_progress: 0, failed: 0, new_resumes_24h: 0, applications: 0, strong_matches: 0 },
      })
    }
    const detail = /^\/applications\/([^/]+)$/.exec(path)
    if (request.method === "GET" && detail) {
      const found = applications.find((application) => application.id === detail[1])
      return found ? json(found) : problem(404, "Not Found", "Application not found")
    }
    return problem(404, "Not Found")
  })
  vi.stubGlobal("fetch", fetchMock)

  /** The calls to one endpoint, e.g. `callsTo("GET", /\/applications$/)`. */
  const callsTo = (method: string, pattern: RegExp) =>
    calls.filter((call) => call.method === method && pattern.test(call.path))
  return { calls, callsTo, fetchMock }
}

export const LIST_PATH = /^\/campaigns\/[^/]+\/applications$/

export function renderWithProviders(
  ui: ReactElement,
  {
    searchParams = "",
    onUrlUpdate,
    queryClient = makeQueryClient(),
  }: { searchParams?: string; onUrlUpdate?: (event: UrlUpdateEvent) => void; queryClient?: QueryClient } = {},
) {
  const result = render(
    <QueryClientProvider client={queryClient}>
      <NuqsTestingAdapter searchParams={searchParams} onUrlUpdate={onUrlUpdate} hasMemory>
        {ui}
      </NuqsTestingAdapter>
    </QueryClientProvider>,
  )
  return { ...result, queryClient }
}

/** A promise with its resolve exposed (to hold a request open). */
export function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}
