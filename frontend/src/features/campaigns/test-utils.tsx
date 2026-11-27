import { QueryClientProvider } from "@tanstack/react-query"
import { render } from "@testing-library/react"
import { NuqsTestingAdapter, type OnUrlUpdateFunction } from "nuqs/adapters/testing"
import { vi } from "vitest"

import type { Schema } from "@/lib/api/client"
import { PROBLEM_MEDIA_TYPE } from "@/lib/api/problem"
import { makeQueryClient } from "@/lib/query/query-client"

/*
 * Test helpers for the campaigns feature: the app's real QueryClient (minus
 * retries) and nuqs's testing adapter around the UI, and a stubbed `fetch`
 * that answers `/api/v2` requests by method and path, the same way
 * src/lib/api/client.test.ts stubs it.
 */

type Handler = (request: Request, match: { url: URL; params: Record<string, string> }) => Response | Promise<Response>

/**
 * Stubs `fetch` for the generated client. Routes are `"METHOD /path"` with
 * `{name}` segments, relative to `/api/v2`, e.g. `"GET /campaigns/{id}"`.
 * An unmatched request fails the test loudly with a 599.
 */
export function mockApi(routes: Record<string, Handler>) {
  const compiled = Object.entries(routes).map(([route, handler]) => {
    const [method, path] = route.split(" ")
    const names: string[] = []
    const pattern = new RegExp(
      `^/api/v2${path.replace(/\{(\w+)\}/g, (_, name: string) => {
        names.push(name)
        return "([^/]+)"
      })}$`,
    )
    return { method, pattern, names, handler }
  })
  const fetchMock = vi.fn(async (request: Request) => {
    const url = new URL(request.url)
    for (const { method, pattern, names, handler } of compiled) {
      const found = request.method === method ? pattern.exec(url.pathname) : null
      if (!found) continue
      const params = Object.fromEntries(names.map((name, index) => [name, decodeURIComponent(found[index + 1])]))
      return handler(request, { url, params })
    }
    return new Response(`No mock for ${request.method} ${url.pathname}`, { status: 599 })
  })
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })
}

export function problem(status: number, title: string, detail?: string, errors?: Schema<"FieldError">[]): Response {
  return new Response(JSON.stringify({ type: "about:blank", title, status, detail, errors }), {
    status,
    headers: { "Content-Type": PROBLEM_MEDIA_TYPE },
  })
}

/** A promise the test resolves by hand, to observe loading states. */
export function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

export function renderWithProviders(
  ui: React.ReactElement,
  { searchParams = "", onUrlUpdate }: { searchParams?: string; onUrlUpdate?: OnUrlUpdateFunction } = {},
) {
  const queryClient = makeQueryClient()
  queryClient.setDefaultOptions({
    queries: { ...queryClient.getDefaultOptions().queries, retry: false },
  })
  const result = render(
    <QueryClientProvider client={queryClient}>
      <NuqsTestingAdapter searchParams={searchParams} onUrlUpdate={onUrlUpdate} hasMemory>
        {ui}
      </NuqsTestingAdapter>
    </QueryClientProvider>,
  )
  return { ...result, queryClient }
}

export const CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"

export function campaignFixture(overrides: Partial<Schema<"Campaign">> = {}): Schema<"Campaign"> {
  return {
    id: CAMPAIGN_ID,
    title: "Backend hire, Q1",
    role: "Backend Engineer",
    description: "Build our APIs.",
    required_skills: ["Python", "PostgreSQL"],
    status: "active",
    client: { id: "00000000-0000-4000-8000-00000000c001", name: "General" },
    created_at: "2026-11-10T09:00:00Z",
    updated_at: "2026-11-10T09:00:00Z",
    last_activity_at: "2026-11-17T09:00:00Z",
    counts: { resumes: 12, in_progress: 2, failed: 1, new_resumes_24h: 3, applications: 9, strong_matches: 4 },
    ...overrides,
  }
}

export function pageOf<T>(items: T[], { total = items.length, page = 1, page_size = 20 } = {}) {
  return { items, total, page, page_size }
}

export function statsFixture(overrides: Partial<Schema<"CampaignStats">> = {}): Schema<"CampaignStats"> {
  return {
    resumes: { total: 12, pending: 1, extracting: 0, processing: 1, done: 9, failed: 1, quota_exceeded: 0 },
    applications: {
      total: 9,
      by_category: { strong_match: 4, moderate_match: 3, weak_match: 1, rejected: 1 },
      by_stage: {
        unreviewed: 9,
        screened: 0,
        phone_call: 0,
        technical: 0,
        offer: 0,
        hired: 0,
        rejected_manual: 0,
      },
    },
    avg_score: 71.4,
    score_histogram: [],
    top_skills: [],
    ...overrides,
  }
}
