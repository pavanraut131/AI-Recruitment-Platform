import { beforeEach, describe, expect, it, vi } from "vitest"

import { ApiProblem } from "@/lib/api/client"

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
})
