import { afterEach, describe, expect, it, vi } from "vitest"

import { ApiProblem, createApiClient, unwrap } from "./client"
import { errorMessage, NETWORK_ERROR_STATUS, PROBLEM_MEDIA_TYPE } from "./problem"

const BASE = "http://api.test/api/v2"

function stubFetch(respond: (request: Request) => Response | Promise<Response>) {
  const fetchMock = vi.fn(async (request: Request) => respond(request))
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

function json(body: unknown, status = 200, contentType = "application/json") {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": contentType } })
}

async function rejection(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise
  } catch (error) {
    return error
  }
  throw new Error("expected the request to fail")
}

const page = { items: [], total: 0, page: 1, page_size: 20 }

describe("the /api/v2 client", () => {
  it("prefixes the base URL and serialises repeatable query parameters as FastAPI expects", async () => {
    const fetchMock = stubFetch(() => json(page))
    const api = createApiClient(BASE)

    const result = await api.GET("/campaigns/{campaign_id}/applications", {
      params: {
        path: { campaign_id: "c-1" },
        query: { category: ["strong_match", "weak_match"], min_score: 50, q: null, page: 2 },
      },
    })

    expect(unwrap(result)).toEqual(page)
    const request = fetchMock.mock.calls[0][0]
    const url = new URL(request.url)
    expect(`${url.origin}${url.pathname}`).toBe(`${BASE}/campaigns/c-1/applications`)
    expect(url.searchParams.getAll("category")).toEqual(["strong_match", "weak_match"])
    expect(url.searchParams.get("min_score")).toBe("50")
    expect(url.searchParams.has("q")).toBe(false)
    expect(url.searchParams.get("page")).toBe("2")
  })

  it("maps a 422 problem+json into an ApiProblem with field errors", async () => {
    stubFetch(() =>
      json(
        {
          type: "about:blank",
          title: "Unprocessable Entity",
          status: 422,
          detail: "2 invalid fields",
          errors: [
            { loc: ["body", "title"], msg: "String should have at least 1 character" },
            { loc: ["body", "required_skills", 0], msg: "Input should be a valid string" },
          ],
        },
        422,
        PROBLEM_MEDIA_TYPE,
      ),
    )
    const api = createApiClient(BASE)

    const error = await rejection(
      api.POST("/campaigns", {
        body: { title: "", role: "Engineer", required_skills: [], status: "active" },
      }),
    )

    expect(error).toBeInstanceOf(ApiProblem)
    const problem = error as ApiProblem
    expect(problem).toMatchObject({
      name: "ApiProblem",
      status: 422,
      title: "Unprocessable Entity",
      type: "about:blank",
      detail: "2 invalid fields",
      message: "2 invalid fields",
    })
    expect(problem.isClientError).toBe(true)
    expect(problem.isServerError).toBe(false)
    expect(problem.fieldErrors()).toEqual({
      title: "String should have at least 1 character",
      "required_skills.0": "Input should be a valid string",
    })
  })

  it("maps a 404 problem without errors or detail", async () => {
    stubFetch(() => json({ type: "about:blank", title: "Not Found", status: 404 }, 404, PROBLEM_MEDIA_TYPE))
    const api = createApiClient(BASE)

    const problem = (await rejection(
      api.GET("/campaigns/{campaign_id}", { params: { path: { campaign_id: "missing" } } }),
    )) as ApiProblem

    expect(problem).toBeInstanceOf(ApiProblem)
    expect(problem.status).toBe(404)
    expect(problem.detail).toBeUndefined()
    expect(problem.errors).toEqual([])
    expect(problem.message).toBe("Not Found")
    expect(errorMessage(problem)).toEqual({ title: "Not Found" })
  })

  it("maps a 500 problem as a server error", async () => {
    stubFetch(() =>
      json({ type: "about:blank", title: "Internal Server Error", status: 500 }, 500, PROBLEM_MEDIA_TYPE),
    )
    const problem = (await rejection(createApiClient(BASE).GET("/stats"))) as ApiProblem

    expect(problem.status).toBe(500)
    expect(problem.isServerError).toBe(true)
  })

  it("builds a problem from the status line when the body is not JSON", async () => {
    stubFetch(
      () =>
        new Response("<html>Bad gateway</html>", {
          status: 502,
          statusText: "Bad Gateway",
          headers: { "Content-Type": "text/html" },
        }),
    )
    const problem = (await rejection(createApiClient(BASE).GET("/home"))) as ApiProblem

    expect(problem).toBeInstanceOf(ApiProblem)
    expect(problem).toMatchObject({ status: 502, title: "Bad Gateway", detail: undefined })
  })

  it("keeps the message of a JSON error that is not a problem, and survives broken JSON", async () => {
    stubFetch(() => json({ detail: "Campaign not found" }, 404))
    const plain = (await rejection(createApiClient(BASE).GET("/home"))) as ApiProblem
    expect(plain).toMatchObject({ status: 404, title: "HTTP 404", detail: "Campaign not found" })

    stubFetch(
      () => new Response("{not json", { status: 500, headers: { "Content-Type": PROBLEM_MEDIA_TYPE } }),
    )
    const broken = (await rejection(createApiClient(BASE).GET("/home"))) as ApiProblem
    expect(broken).toMatchObject({ status: 500, title: "HTTP 500", detail: undefined })
  })

  it("ignores malformed field errors instead of failing", async () => {
    stubFetch(() =>
      json(
        { title: "Unprocessable Entity", status: 422, errors: [{ loc: "body", msg: 1 }, { loc: ["query", "page"], msg: "bad" }] },
        422,
        PROBLEM_MEDIA_TYPE,
      ),
    )
    const problem = (await rejection(createApiClient(BASE).GET("/campaigns"))) as ApiProblem

    expect(problem.errors).toEqual([{ loc: ["query", "page"], msg: "bad" }])
    expect(problem.fieldErrors()).toEqual({ page: "bad" })
  })

  it("maps a request that got no response to a network problem (status 0)", async () => {
    const offline = new TypeError("Failed to fetch")
    stubFetch(() => {
      throw offline
    })
    const problem = (await rejection(createApiClient(BASE).GET("/home"))) as ApiProblem

    expect(problem).toBeInstanceOf(ApiProblem)
    expect(problem.status).toBe(NETWORK_ERROR_STATUS)
    expect(problem.isNetworkError).toBe(true)
    expect(problem.isClientError || problem.isServerError).toBe(false)
    expect(problem.cause).toBe(offline)
    expect(problem.title).toBe("Network error")
  })

  it("rethrows an aborted request unchanged (a cancelled query is not a network error)", async () => {
    stubFetch(() => {
      throw new DOMException("The operation was aborted.", "AbortError")
    })
    const error = await rejection(createApiClient(BASE).GET("/home"))

    expect(error).not.toBeInstanceOf(ApiProblem)
    expect((error as Error).name).toBe("AbortError")
  })

  it("returns no data for a 204", async () => {
    stubFetch(() => new Response(null, { status: 204 }))
    const result = await createApiClient(BASE).DELETE("/campaigns/{campaign_id}", {
      params: { path: { campaign_id: "c-1" } },
    })

    expect(unwrap(result)).toBeUndefined()
  })
})

describe("API_V2_BASE_URL", () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it("is NEXT_PUBLIC_API_URL plus /api/v2, without a doubled slash", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://hireflow.example/")
    vi.resetModules()
    const { API_V2_BASE_URL } = await import("./client")
    expect(API_V2_BASE_URL).toBe("https://hireflow.example/api/v2")
  })

  it("defaults to the local backend", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "")
    vi.resetModules()
    const { API_V2_BASE_URL } = await import("./client")
    expect(API_V2_BASE_URL).toBe("http://localhost:8000/api/v2")
  })
})
