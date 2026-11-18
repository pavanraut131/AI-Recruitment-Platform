import { vi } from "vitest"

import type { Schema } from "@/lib/api/client"
import { json, problem, toApiCall, type ApiCall } from "@/test/helpers"

import { makeApplication } from "./fixtures"
import type { Application, BulkResult } from "./model"

/**
 * Test helpers for the applications feature: a fake `/api/v2` behind `fetch`
 * (the shared helpers in `@/test/helpers` do the rest).
 */

export {
  deferred,
  installDomPolyfills,
  json,
  problem,
  renderWithProviders,
  type ApiCall,
} from "@/test/helpers"

export const CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"

type Person = Schema<"Person">
type Override = (call: ApiCall) => Response | Promise<Response> | undefined

/**
 * A fake `/api/v2` on `fetch`: lists `applications` (paged by `page` and
 * `page_size`; filters are asserted from the recorded calls, not applied),
 * answers the campaign, one application, its PATCH (applied to the fake's
 * copy, so a refetch shows it), bulk actions, the CSV export and `people`.
 * `override` answers a call first when it returns a response.
 */
export function installFakeApi({
  applications = Array.from({ length: 3 }, (_, index) => makeApplication(index)),
  total,
  requiredSkills = ["React", "PostgreSQL"],
  people = [],
  override,
}: {
  applications?: Application[]
  total?: number
  requiredSkills?: string[]
  people?: Person[]
  override?: Override
} = {}) {
  const calls: ApiCall[] = []
  // The server's copy: PATCHes change it.
  const store = applications.map((application) => ({ ...application }))
  const fetchMock = vi.fn(async (request: Request) => {
    const call = await toApiCall(request)
    const { path, url, body } = call
    calls.push(call)
    const custom = await override?.(call)
    if (custom) return custom

    if (request.method === "GET" && /^\/campaigns\/[^/]+\/applications$/.test(path)) {
      const page = Number(url.searchParams.get("page") ?? 1)
      const size = Number(url.searchParams.get("page_size") ?? 20)
      const items = store.slice((page - 1) * size, page * size)
      return json({ items, total: total ?? store.length, page, page_size: size })
    }
    if (request.method === "GET" && /^\/campaigns\/[^/]+\/applications\/export$/.test(path)) {
      return new Response("name,email\n", { headers: { "Content-Type": "text/csv" } })
    }
    if (request.method === "POST" && /^\/campaigns\/[^/]+\/applications\/bulk$/.test(path)) {
      const { action, ids } = body as { action: BulkResult["action"]; ids: string[] }
      const known = new Set(store.map((application) => application.id))
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
    if (detail) {
      const index = store.findIndex((application) => application.id === detail[1])
      if (index === -1) return problem(404, "Not Found", "Application not found")
      if (request.method === "PATCH") {
        store[index] = { ...store[index], ...(body as Partial<Application>), updated_at: "2026-11-18T12:00:00Z" }
      }
      if (request.method === "GET" || request.method === "PATCH") return json(store[index])
    }
    const person = /^\/people\/([^/]+)$/.exec(path)
    if (request.method === "GET" && person) {
      const found = people.find((candidate) => candidate.id === person[1])
      return found ? json(found) : problem(404, "Not Found", "Person not found")
    }
    return problem(404, "Not Found")
  })
  vi.stubGlobal("fetch", fetchMock)

  /** The calls to one endpoint, e.g. `callsTo("GET", /\/applications$/)`. */
  const callsTo = (method: string, pattern: RegExp) =>
    calls.filter((call) => call.method === method && pattern.test(call.path))
  return { calls, callsTo, fetchMock, store }
}

export const LIST_PATH = /^\/campaigns\/[^/]+\/applications$/
