import { vi } from "vitest"

import type { Schema } from "@/lib/api/client"
import { json, problem, toApiCall, type ApiCall } from "@/test/helpers"

type Application = Schema<"Application">
type Person = Schema<"Person">
type Override = (call: ApiCall) => Response | Promise<Response> | undefined

/** A full application (`GET /applications/{id}`) for a Person's application. */
export function makeApplicationDetail(
  id: string,
  { campaignId, personId, ...overrides }: Partial<Application> & { campaignId: string; personId: string },
): Application {
  return {
    id,
    campaign_id: campaignId,
    resume_id: `resume-${id}`,
    person: { id: personId, name: "Ada Lovelace", email: "ada@example.com" },
    score: 80,
    score_breakdown: null,
    category: "strong_match",
    summary: null,
    strengths: [],
    weaknesses: [],
    missing_skills: [],
    recommendation: null,
    skills: [],
    years_of_experience: 6,
    pipeline_stage: null,
    notes: null,
    created_at: "2026-11-01T09:00:00Z",
    updated_at: "2026-11-01T09:00:00Z",
    ...overrides,
  }
}

/**
 * A fake `/api/v2` on `fetch` for the Person profile: `GET /people/{id}`,
 * `GET /applications/{id}` and its PATCH (applied to the fake's copy).
 */
export function installPeopleApi({
  people,
  applications,
  override,
}: {
  people: Person[]
  applications: Application[]
  override?: Override
}) {
  const calls: ApiCall[] = []
  const store = applications.map((application) => ({ ...application }))
  const fetchMock = vi.fn(async (request: Request) => {
    const call = await toApiCall(request)
    calls.push(call)
    const custom = await override?.(call)
    if (custom) return custom

    const person = /^\/people\/([^/]+)$/.exec(call.path)
    if (call.method === "GET" && person) {
      const found = people.find((candidate) => candidate.id === person[1])
      return found ? json(found) : problem(404, "Not Found", "Person not found")
    }
    const detail = /^\/applications\/([^/]+)$/.exec(call.path)
    if (detail) {
      const index = store.findIndex((application) => application.id === detail[1])
      if (index === -1) return problem(404, "Not Found", "Application not found")
      if (call.method === "PATCH") store[index] = { ...store[index], ...(call.body as Partial<Application>) }
      return json(store[index])
    }
    return problem(404, "Not Found")
  })
  vi.stubGlobal("fetch", fetchMock)
  const callsTo = (method: string, pattern: RegExp) =>
    calls.filter((call) => call.method === method && pattern.test(call.path))
  return { calls, callsTo, store }
}
