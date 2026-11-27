import { QueryClientProvider, type QueryClient } from "@tanstack/react-query"
import { render } from "@testing-library/react"
import { NuqsTestingAdapter, type UrlUpdateEvent } from "nuqs/adapters/testing"
import type { ReactElement } from "react"
import { vi } from "vitest"

import type { Schema } from "@/lib/api/client"
import { makeQueryClient } from "@/lib/query/query-client"

import { signInForTest } from "./auth"

/**
 * Test helpers shared by the features' tests (Vitest + Testing Library):
 * DOM APIs that jsdom lacks (Radix needs them), JSON and problem+json
 * responses for a fake `/api/v2` on `fetch`, and a render with the Query and
 * nuqs providers. Each feature builds its own fake API on top.
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

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  })
}

export function problem(status: number, title: string, detail?: string): Response {
  return json({ type: "about:blank", title, status, ...(detail ? { detail } : {}) }, status)
}

/** One request seen by a fake API. */
export type ApiCall = { method: string; path: string; url: URL; body: unknown }

/** Parse a `fetch` request to the fake `/api/v2` into an {@link ApiCall}. */
export async function toApiCall(request: Request): Promise<ApiCall> {
  const url = new URL(request.url)
  const path = url.pathname.replace(/^\/api\/v2/, "")
  const body = request.method === "GET" ? undefined : await request.clone().json().catch(() => undefined)
  return { method: request.method, path, url, body }
}

export function renderWithProviders(
  ui: ReactElement,
  {
    searchParams = "",
    onUrlUpdate,
    queryClient = makeQueryClient(),
    me,
  }: {
    searchParams?: string
    onUrlUpdate?: (event: UrlUpdateEvent) => void
    queryClient?: QueryClient
    /** The signed-in user (default: an owner, every permission; `null`: none cached). */
    me?: Schema<"Me"> | null
  } = {},
) {
  signInForTest(queryClient, me)
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
