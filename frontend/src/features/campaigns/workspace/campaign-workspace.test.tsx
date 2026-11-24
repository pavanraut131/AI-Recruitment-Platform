import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import type { UrlUpdateEvent } from "nuqs/adapters/testing"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { useCampaignEvents } from "@/lib/query/use-campaign-events"
import { installDomPolyfills } from "@/test/helpers"

import {
  CAMPAIGN_ID,
  campaignFixture,
  json,
  mockApi,
  problem,
  renderWithProviders,
  statsFixture,
} from "../test-utils"
import { CampaignWorkspace } from "./campaign-workspace"

const { push } = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock("@/lib/query/use-campaign-events", () => ({ useCampaignEvents: vi.fn(() => "open") }))

const campaign = campaignFixture()

beforeEach(() => {
  installDomPolyfills()
  push.mockClear()
  vi.mocked(useCampaignEvents).mockClear()
})

function renderWorkspace(searchParams = "") {
  const urlUpdates: UrlUpdateEvent[] = []
  const view = renderWithProviders(
    <CampaignWorkspace
      campaignId={CAMPAIGN_ID}
      candidatesTab={<p>Candidates slot</p>}
      uploadsTab={<p>Uploads slot</p>}
    />,
    { searchParams, onUrlUpdate: (event) => urlUpdates.push(event) },
  )
  return { ...view, lastUrl: () => urlUpdates.at(-1)?.queryString }
}

function mockCampaignApi(overrides: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    "GET /campaigns/{id}": () => json(campaign),
    "GET /campaigns/{id}/stats": () => json(statsFixture()),
    ...overrides,
  })
}

describe("CampaignWorkspace", () => {
  it("shows the header, the summary sentence and the Candidates slot by default, and streams the campaign's events", async () => {
    mockCampaignApi()
    renderWorkspace()

    expect(screen.getByLabelText("Loading campaign")).toBeTruthy()
    expect(await screen.findByRole("heading", { level: 1, name: campaign.title })).toBeTruthy()
    expect(screen.getByText(campaign.role)).toBeTruthy()
    expect(screen.getByRole("status").textContent).toBe("Live")

    // The numbers sit in one quiet row (no KPI cards): the count at once, the split and average with the stats,
    // then a note for anything still processing or failed.
    const items = () =>
      within(screen.getByRole("list", { name: "Applicants" }))
        .getAllByRole("listitem")
        .map((item) => item.textContent)
    await waitFor(() =>
      expect(items()).toEqual(["9 applicants", "4 strong", "3 possible", "1 unlikely", "1 not a match", "Average score 71"]),
    )
    expect(document.querySelector("[data-slot='campaign-summary'] p")?.textContent).toBe(
      "2 resumes are still being read and scored. 1 resume could not be processed: see Uploads.",
    )

    expect(screen.getByRole("tab", { name: "Candidates", selected: true })).toBeTruthy()
    expect(screen.getByRole("tab", { name: "Brief" })).toBeTruthy()
    expect(screen.getByText("Candidates slot")).toBeTruthy()
    expect(useCampaignEvents).toHaveBeenLastCalledWith(CAMPAIGN_ID, { enabled: true })
  })

  it("opens the tab named in the URL", async () => {
    mockCampaignApi()
    renderWorkspace("?tab=insights")

    expect(await screen.findByRole("tab", { name: "Insights", selected: true })).toBeTruthy()
    expect(await screen.findByRole("heading", { name: "Score histogram" })).toBeTruthy()
    expect(screen.queryByText("Candidates slot")).toBeNull()
  })

  it("keeps the selected tab in the URL, leaving out the default", async () => {
    mockCampaignApi()
    const view = renderWorkspace()
    await screen.findByRole("heading", { level: 1 })

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Uploads" }))
    await waitFor(() => expect(view.lastUrl()).toBe("?tab=uploads"))
    expect(screen.getByText("Uploads slot")).toBeTruthy()
    expect(screen.queryByText("Candidates slot")).toBeNull()

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Candidates" }))
    await waitFor(() => expect(view.lastUrl()).toBe(""))

    // The header's actions switch tabs too.
    fireEvent.click(screen.getByRole("button", { name: "Edit brief" }))
    await waitFor(() => expect(view.lastUrl()).toBe("?tab=settings"))
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveProperty("value", campaign.title)
  })

  it("says so when the campaign does not exist, without streaming", async () => {
    mockCampaignApi({ "GET /campaigns/{id}": () => problem(404, "Not Found", "Campaign not found") })
    renderWorkspace()

    expect(await screen.findByText("Campaign not found")).toBeTruthy()
    expect(screen.getByRole("link", { name: "All campaigns" }).getAttribute("href")).toBe("/campaigns")
    expect(useCampaignEvents).toHaveBeenLastCalledWith(CAMPAIGN_ID, { enabled: false })
  })

  it("shows other errors with a retry", async () => {
    let calls = 0
    mockCampaignApi({
      "GET /campaigns/{id}": () => {
        calls += 1
        return calls === 1 ? problem(500, "Internal Server Error", "Try later.") : json(campaign)
      },
    })
    renderWorkspace()

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("Try later.")
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }))

    expect(await screen.findByRole("heading", { level: 1, name: campaign.title })).toBeTruthy()
  })
})

describe("Settings tab", () => {
  it("saves edited fields and status with PATCH", async () => {
    const fetchMock = mockCampaignApi({
      "PATCH /campaigns/{id}": async (request) => json({ ...campaign, ...(await request.clone().json()) }),
    })
    renderWorkspace("?tab=settings")

    const title = await screen.findByRole("textbox", { name: "Title" })
    const save = screen.getByRole("button", { name: "Save changes" })
    expect(save.hasAttribute("disabled")).toBe(true)

    fireEvent.change(title, { target: { value: "Backend hire, Q2" } })
    fireEvent.click(save)

    await waitFor(() => expect(fetchMock.mock.calls.some(([request]) => request.method === "PATCH")).toBe(true))
    const patch = fetchMock.mock.calls.find(([request]) => request.method === "PATCH")![0]
    expect(new URL(patch.url).pathname).toBe(`/api/v2/campaigns/${CAMPAIGN_ID}`)
    expect(await patch.json()).toEqual({
      title: "Backend hire, Q2",
      role: campaign.role,
      description: campaign.description,
      required_skills: campaign.required_skills,
      status: "active",
    })
    await waitFor(() => expect(screen.getByRole("button", { name: "Save changes" }).hasAttribute("disabled")).toBe(true))
  })

  it("asks for confirmation before deleting, and Cancel deletes nothing", async () => {
    const fetchMock = mockCampaignApi({ "DELETE /campaigns/{id}": () => new Response(null, { status: 204 }) })
    renderWorkspace("?tab=settings")

    fireEvent.click(await screen.findByRole("button", { name: "Delete campaign" }))

    const dialog = await screen.findByRole("alertdialog")
    expect(within(dialog).getByText(`Delete “${campaign.title}”?`)).toBeTruthy()
    expect(within(dialog).getByText(/9 applications and 12 resumes/)).toBeTruthy()

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }))

    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull())
    expect(fetchMock.mock.calls.some(([request]) => request.method === "DELETE")).toBe(false)
    expect(push).not.toHaveBeenCalled()
  })

  it("deletes on confirmation and returns to the campaign list", async () => {
    const fetchMock = mockCampaignApi({ "DELETE /campaigns/{id}": () => new Response(null, { status: 204 }) })
    renderWorkspace("?tab=settings")

    fireEvent.click(await screen.findByRole("button", { name: "Delete campaign" }))
    const dialog = await screen.findByRole("alertdialog")
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete campaign" }))

    await waitFor(() => expect(push).toHaveBeenCalledWith("/campaigns"))
    const request = fetchMock.mock.calls.find(([call]) => call.method === "DELETE")![0]
    expect(new URL(request.url).pathname).toBe(`/api/v2/campaigns/${CAMPAIGN_ID}`)
  })

  it("keeps the dialog open when the delete fails", async () => {
    const fetchMock = mockCampaignApi({ "DELETE /campaigns/{id}": () => problem(500, "Internal Server Error") })
    renderWorkspace("?tab=settings")

    fireEvent.click(await screen.findByRole("button", { name: "Delete campaign" }))
    const dialog = await screen.findByRole("alertdialog")
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete campaign" }))

    await waitFor(() => expect(fetchMock.mock.calls.some(([request]) => request.method === "DELETE")).toBe(true))
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Delete campaign" }).hasAttribute("disabled")).toBe(false),
    )
    expect(screen.getByRole("alertdialog")).toBeTruthy()
    expect(push).not.toHaveBeenCalled()
  })
})
