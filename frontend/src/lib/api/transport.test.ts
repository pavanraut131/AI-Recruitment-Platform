import { afterEach, beforeEach, describe, expect, it } from "vitest"

import { clearCsrfCookie, installApi, json, noContent, problem } from "@/test/auth"

import { ApiProblem, createApiClient, type Schema } from "./client"
import { CSRF_FAILURE_DETAIL, CSRF_HEADER, forgetCsrfToken, readCsrfCookie, rememberCsrfToken } from "./csrf"
import { onSessionEvent, type SessionEvent } from "./transport"

const BASE = "http://api.test/api/v2"
const CAMPAIGN: Schema<"CampaignCreate"> = { title: "Backend hire", role: "Engineer", required_skills: [], status: "active" }

async function rejection(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise
  } catch (error) {
    return error
  }
  throw new Error("expected the request to fail")
}

let events: SessionEvent[]
let stopListening: () => void

beforeEach(() => {
  events = []
  stopListening = onSessionEvent((event) => events.push(event))
})

afterEach(() => {
  stopListening()
})

describe("credentials", () => {
  it("sends the session cookie with every request (credentials: include)", async () => {
    const calls = installApi({
      "GET /home": () => json({}),
      "POST /campaigns": () => json({}, 201),
    })
    const api = createApiClient(BASE)

    await api.GET("/home")
    await api.POST("/campaigns", { body: CAMPAIGN })

    expect(calls.map((call) => call.credentials)).toEqual(["include", "include"])
  })
})

describe("the CSRF header", () => {
  it("is sent on unsafe methods only, from the readable CSRF cookie", async () => {
    document.cookie = "hf_csrf=cookie-token; path=/"
    const calls = installApi({
      "GET /home": () => json({}),
      "POST /campaigns": () => json({}, 201),
      "PATCH /campaigns/c-1": () => json({}),
      "DELETE /campaigns/c-1": () => noContent(),
    })
    const api = createApiClient(BASE)

    await api.GET("/home")
    await api.POST("/campaigns", { body: CAMPAIGN })
    await api.PATCH("/campaigns/{campaign_id}", { params: { path: { campaign_id: "c-1" } }, body: { title: "x" } })
    await api.DELETE("/campaigns/{campaign_id}", { params: { path: { campaign_id: "c-1" } } })

    expect(calls.map((call) => [call.method, call.headers.get(CSRF_HEADER)])).toEqual([
      ["GET", null],
      ["POST", "cookie-token"],
      ["PATCH", "cookie-token"],
      ["DELETE", "cookie-token"],
    ])
  })

  it("prefers the Secure cookie's name, and reads a value with an equals sign", () => {
    expect(readCsrfCookie("a=1; hf_csrf=plain; __Host-hf_csrf=se.cu=re")).toBe("se.cu=re")
    expect(readCsrfCookie("hf_csrf=plain")).toBe("plain")
    expect(readCsrfCookie("other=1")).toBeNull()
  })

  it("without a readable cookie, fetches one token from GET /auth/csrf for concurrent requests", async () => {
    clearCsrfCookie()
    const calls = installApi({
      "GET /auth/csrf": () => json({ csrf_token: "fetched-token" }),
      "POST /campaigns": () => json({}, 201),
    })
    const api = createApiClient(BASE)

    await Promise.all([api.POST("/campaigns", { body: CAMPAIGN }), api.POST("/campaigns", { body: CAMPAIGN })])

    expect(calls.filter((call) => call.path === "/auth/csrf")).toHaveLength(1)
    expect(calls.find((call) => call.path === "/auth/csrf")?.credentials).toBe("include")
    const posts = calls.filter((call) => call.method === "POST")
    expect(posts.map((call) => call.headers.get(CSRF_HEADER))).toEqual(["fetched-token", "fetched-token"])
  })

  it("uses a token the API returned in a body (Me.csrf_token) when the cookie is not readable", async () => {
    clearCsrfCookie()
    rememberCsrfToken("from-me")
    const calls = installApi({ "POST /campaigns": () => json({}, 201) })

    await createApiClient(BASE).POST("/campaigns", { body: CAMPAIGN })

    expect(calls).toHaveLength(1)
    expect(calls[0].headers.get(CSRF_HEADER)).toBe("from-me")
    forgetCsrfToken()
  })

  it("repeats a request once, with a fresh token and the same body, after a CSRF 403", async () => {
    document.cookie = "hf_csrf=stale-token; path=/"
    let posts = 0
    const calls = installApi({
      "GET /auth/csrf": () => json({ csrf_token: "fresh-token" }),
      "POST /campaigns": () => (++posts === 1 ? problem(403, CSRF_FAILURE_DETAIL) : json({ id: "c-1" }, 201)),
    })

    const { data } = await createApiClient(BASE).POST("/campaigns", { body: CAMPAIGN })

    expect(data).toEqual({ id: "c-1" })
    const sent = calls.filter((call) => call.method === "POST")
    expect(sent.map((call) => call.headers.get(CSRF_HEADER))).toEqual(["stale-token", "fresh-token"])
    expect(sent[1].body).toEqual(CAMPAIGN)
  })

  it("does not repeat it twice: a second CSRF 403 is the request's answer", async () => {
    const calls = installApi({
      "GET /auth/csrf": () => json({ csrf_token: "fresh-token" }),
      "POST /campaigns": () => problem(403, CSRF_FAILURE_DETAIL),
    })

    const error = await rejection(createApiClient(BASE).POST("/campaigns", { body: CAMPAIGN }))

    expect(error).toBeInstanceOf(ApiProblem)
    expect((error as ApiProblem).status).toBe(403)
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(2)
  })

  it("does not repeat a 403 that is about permissions", async () => {
    const calls = installApi({
      "POST /campaigns": () => problem(403, "You don't have permission to create or edit campaigns"),
    })

    await rejection(createApiClient(BASE).POST("/campaigns", { body: CAMPAIGN }))

    expect(calls).toHaveLength(1)
  })

  it("fails with the CSRF endpoint's problem when no token can be had", async () => {
    clearCsrfCookie()
    installApi({ "GET /auth/csrf": () => problem(500) })

    const error = await rejection(createApiClient(BASE).POST("/campaigns", { body: CAMPAIGN }))

    expect(error).toBeInstanceOf(ApiProblem)
    expect((error as ApiProblem).status).toBe(500)
  })
})

describe("session problems", () => {
  it("reports a 401 from any request as an ended session", async () => {
    installApi({ "GET /home": () => problem(401, "Your session has ended. Sign in again.") })

    const error = await rejection(createApiClient(BASE).GET("/home"))

    expect((error as ApiProblem).status).toBe(401)
    expect(events).toEqual(["unauthorized"])
  })

  it("does not report the sign-in endpoints' own 401 (a wrong password)", async () => {
    installApi({ "POST /auth/login": () => problem(401, "Invalid email or password") })

    await rejection(createApiClient(BASE).POST("/auth/login", { body: { email: "a@b.c", password: "x" } }))

    expect(events).toEqual([])
  })

  it("reports a 403 'Choose an agency', and no other 403", async () => {
    installApi({
      "GET /home": () => problem(403, "Choose an agency"),
      "GET /stats": () => problem(403, "You don't have permission to see the team"),
    })
    const api = createApiClient(BASE)

    await rejection(api.GET("/home"))
    await rejection(api.GET("/stats"))

    expect(events).toEqual(["agency-required"])
  })

  it("reads a 429's Retry-After into the problem", async () => {
    installApi({
      "POST /auth/login": () => problem(429, "Too many attempts. Try again later.", {}, { "Retry-After": "840" }),
    })

    const error = await rejection(createApiClient(BASE).POST("/auth/login", { body: { email: "a@b.c", password: "x" } }))

    expect(error).toMatchObject({ status: 429, retryAfter: 840 })
  })
})
