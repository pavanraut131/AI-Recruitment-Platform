import { screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { json, problem, renderWithProviders, toApiCall, type ApiCall } from "@/test/helpers"

import { CandidateRedirect } from "./candidate-redirect"
import { makeApplication } from "./fixtures"

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }))
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }))

const APPLICATION_ID = "a0000000-0000-4000-8000-000000000001"
const CAMPAIGN_ID = "c0000000-0000-4000-8000-000000000001"

let calls: ApiCall[]

function installApi(response: () => Response) {
  calls = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      calls.push(await toApiCall(request))
      return response()
    }),
  )
}

beforeEach(() => replace.mockClear())
afterEach(() => vi.unstubAllGlobals())

describe("CandidateRedirect (old /candidates/<id> links)", () => {
  it("replaces the URL with the application in its campaign, review panel open", async () => {
    installApi(() => json(makeApplication(0, { id: APPLICATION_ID, campaign_id: CAMPAIGN_ID })))
    renderWithProviders(<CandidateRedirect applicationId={APPLICATION_ID} />)

    expect(screen.getByLabelText("Opening candidate").getAttribute("aria-busy")).toBe("true")
    await waitFor(() => expect(replace).toHaveBeenCalledWith(`/campaigns/${CAMPAIGN_ID}?open=${APPLICATION_ID}`))
    expect(calls.map(({ method, path }) => `${method} ${path}`)).toEqual([`GET /applications/${APPLICATION_ID}`])
  })

  it("says the candidate is not found for an unknown id", async () => {
    installApi(() => problem(404, "Application not found"))
    renderWithProviders(<CandidateRedirect applicationId={APPLICATION_ID} />)

    await screen.findByRole("heading", { name: "Candidate not found" })
    expect(screen.getByRole("link", { name: "Campaigns" }).getAttribute("href")).toBe("/campaigns")
    expect(replace).not.toHaveBeenCalled()
  })

  it("shows other failures with a retry (4xx: not retried automatically)", async () => {
    installApi(() => problem(403, "Not allowed"))
    renderWithProviders(<CandidateRedirect applicationId={APPLICATION_ID} />)

    expect((await screen.findByRole("alert")).textContent).toContain("Not allowed")
    screen.getByRole("button", { name: "Try again" })
  })
})
