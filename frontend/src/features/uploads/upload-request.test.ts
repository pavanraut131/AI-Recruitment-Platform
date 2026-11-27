import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ApiProblem } from "@/lib/api/client"
import { CSRF_FAILURE_DETAIL, CSRF_HEADER } from "@/lib/api/csrf"
import { onSessionEvent, type SessionEvent } from "@/lib/api/transport"
import { clearCsrfCookie, installApi, json } from "@/test/auth"

import { CAMPAIGN_ID, makeFile, makeResume, MockXHR } from "./test-utils"
import { postUpload, uploadUrl } from "./upload-request"

beforeEach(() => {
  MockXHR.install()
})

describe("postUpload (uploadResumes over XMLHttpRequest)", () => {
  it("posts the files as multipart field `files` to the campaign's v2 uploads URL", async () => {
    const pending = postUpload(CAMPAIGN_ID, [makeFile("a.pdf"), makeFile("b.pdf")])
    const request = MockXHR.latest()

    expect(request.method).toBe("POST")
    expect(request.url).toBe(uploadUrl(CAMPAIGN_ID))
    expect(request.url).toMatch(/\/api\/v2\/campaigns\/11111111-[\w-]+\/uploads$/)
    expect(request.files().map((file) => file.name)).toEqual(["a.pdf", "b.pdf"])

    const result = { accepted: [makeResume("r-1")], rejected: [{ filename: "b.pdf", reason: "Invalid PDF file" }] }
    request.respond(202, result)
    await expect(pending).resolves.toEqual(result)
  })

  it("reports the fraction of the body sent", async () => {
    const onProgress = vi.fn()
    const pending = postUpload(CAMPAIGN_ID, [makeFile("a.pdf")], { onProgress })
    const request = MockXHR.latest()

    request.progress(25, 100)
    request.progress(100, 100)
    expect(onProgress.mock.calls).toEqual([[0.25], [1]])

    request.respond(202, { accepted: [], rejected: [] })
    await pending
  })

  it("rejects with the problem the server answered (e.g. too many files, 422)", async () => {
    const pending = postUpload(CAMPAIGN_ID, [makeFile("a.pdf")])
    MockXHR.latest().respond(422, {
      type: "about:blank",
      title: "Unprocessable Entity",
      status: 422,
      detail: "Maximum 50 files per upload batch",
    })

    const error = await pending.catch((reason: unknown) => reason)
    expect(error).toBeInstanceOf(ApiProblem)
    expect(error).toMatchObject({ status: 422, title: "Unprocessable Entity", detail: "Maximum 50 files per upload batch" })
  })

  it("rejects with a network problem (status 0) when there is no response", async () => {
    const pending = postUpload(CAMPAIGN_ID, [makeFile("a.pdf")])
    MockXHR.latest().fail()

    const error = await pending.catch((reason: unknown) => reason)
    expect(error).toBeInstanceOf(ApiProblem)
    expect((error as ApiProblem).isNetworkError).toBe(true)
  })

  it("sends the session cookie and the CSRF header (EPIC-02 §9.4)", async () => {
    const pending = postUpload(CAMPAIGN_ID, [makeFile("a.pdf")])
    const request = MockXHR.latest()

    expect(request.withCredentials).toBe(true)
    expect(request.headers[CSRF_HEADER]).toBe("vitest-csrf-token")

    request.respond(202, { accepted: [], rejected: [] })
    await pending
  })

  it("fetches a CSRF token first when the cookie is not readable", async () => {
    clearCsrfCookie()
    const calls = installApi({ "GET /auth/csrf": () => json({ csrf_token: "fetched-token" }) })

    const pending = postUpload(CAMPAIGN_ID, [makeFile("a.pdf")])
    await vi.waitFor(() => expect(MockXHR.instances).toHaveLength(1))

    expect(calls.map((call) => call.path)).toEqual(["/auth/csrf"])
    expect(MockXHR.latest().headers[CSRF_HEADER]).toBe("fetched-token")
    MockXHR.latest().respond(202, { accepted: [], rejected: [] })
    await pending
  })

  it("sends the files again, once, with a fresh token after a CSRF 403", async () => {
    installApi({ "GET /auth/csrf": () => json({ csrf_token: "fresh-token" }) })
    const pending = postUpload(CAMPAIGN_ID, [makeFile("a.pdf")])
    MockXHR.latest().respond(403, { type: "about:blank", title: "Forbidden", status: 403, detail: CSRF_FAILURE_DETAIL })

    await vi.waitFor(() => expect(MockXHR.instances).toHaveLength(2))
    const retry = MockXHR.latest()
    expect(retry.headers[CSRF_HEADER]).toBe("fresh-token")
    expect(retry.files().map((file) => file.name)).toEqual(["a.pdf"])

    retry.respond(202, { accepted: [], rejected: [] })
    await expect(pending).resolves.toEqual({ accepted: [], rejected: [] })
  })

  describe("a 401", () => {
    let events: SessionEvent[]
    let stop: () => void
    beforeEach(() => {
      events = []
      stop = onSessionEvent((event) => events.push(event))
    })
    afterEach(() => stop())

    it("is reported as an ended session, and rejects", async () => {
      const pending = postUpload(CAMPAIGN_ID, [makeFile("a.pdf")])
      MockXHR.latest().respond(401, { type: "about:blank", title: "Unauthorized", status: 401 })

      const error = await pending.catch((reason: unknown) => reason)
      expect((error as ApiProblem).status).toBe(401)
      expect(events).toEqual(["unauthorized"])
    })
  })
})
