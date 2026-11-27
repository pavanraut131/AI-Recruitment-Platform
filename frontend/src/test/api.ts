import { vi } from "vitest"

import type { Schema } from "@/lib/api/client"
import { PROBLEM_MEDIA_TYPE } from "@/lib/api/problem"

/*
 * A fake `/api/v2` on `fetch` with path parameters, for screens that call
 * several endpoints (the settings pages, the shell's switcher): routes are
 * `"METHOD /path"` with `{name}` segments, relative to `/api/v2`, e.g.
 * `"PATCH /team/members/{id}"`. An unrouted request gets a 599, so it fails
 * loudly. Every call is recorded with its parsed JSON body.
 */

export type RouteCall = { method: string; path: string; url: URL; params: Record<string, string>; body: unknown }
type Handler = (call: RouteCall) => Response | Promise<Response>

export function mockRoutes(routes: Record<string, Handler>): RouteCall[] {
  const calls: RouteCall[] = []
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
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url)
      const text = request.method === "GET" ? "" : await request.clone().text()
      let body: unknown
      try {
        body = text ? JSON.parse(text) : undefined
      } catch {
        body = text
      }
      for (const { method, pattern, names, handler } of compiled) {
        const found = request.method === method ? pattern.exec(url.pathname) : null
        if (!found) continue
        const params = Object.fromEntries(names.map((name, index) => [name, decodeURIComponent(found[index + 1])]))
        const call = { method, path: url.pathname.replace(/^\/api\/v2/, ""), url, params, body }
        calls.push(call)
        return handler(call)
      }
      calls.push({ method: request.method, path: url.pathname.replace(/^\/api\/v2/, ""), url, params: {}, body })
      return new Response(`No route for ${request.method} ${url.pathname}`, { status: 599 })
    }),
  )
  return calls
}

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? PROBLEM_MEDIA_TYPE : "application/json", ...headers },
  })
}

export function problem(
  status: number,
  detail?: string,
  { errors, headers = {} }: { errors?: Schema<"FieldError">[]; headers?: Record<string, string> } = {},
): Response {
  const titles: Record<number, string> = {
    403: "Forbidden",
    404: "Not Found",
    409: "Conflict",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
  }
  return json({ type: "about:blank", title: titles[status] ?? "Error", status, detail, errors }, status, headers)
}

export function noContent(): Response {
  return new Response(null, { status: 204 })
}

export function pageOf<T>(items: T[], { total = items.length, page = 1, page_size = 20 } = {}) {
  return { items, total, page, page_size }
}

export function clientFixture(overrides: Partial<Schema<"Client">> = {}): Schema<"Client"> {
  return {
    id: "00000000-0000-4000-8000-00000000c001",
    name: "General",
    status: "active",
    created_at: "2026-11-01T09:00:00Z",
    updated_at: "2026-11-01T09:00:00Z",
    ...overrides,
  }
}

/** Requests that change something (not the GETs the screen loads with). */
export function writesOf(calls: RouteCall[]): RouteCall[] {
  return calls.filter((call) => call.method !== "GET")
}
