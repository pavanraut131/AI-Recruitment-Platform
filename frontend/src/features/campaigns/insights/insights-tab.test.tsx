import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { queryKeys } from "@/lib/api/query-keys"
import { installDomPolyfills } from "@/test/helpers"

import {
  CAMPAIGN_ID,
  campaignFixture,
  deferred,
  json,
  mockApi,
  problem,
  renderWithProviders,
  statsFixture,
} from "../test-utils"
import { InsightsTab } from "./insights-tab"

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

beforeEach(() => {
  installDomPolyfills()
})

const campaign = campaignFixture({ required_skills: ["Python", "PostgreSQL", "Kubernetes"] })

const histogram = Array.from({ length: 10 }, (_, index) => ({
  min: index * 10,
  max: index * 10 + 10,
  count: [0, 0, 0, 1, 1, 0, 2, 1, 3, 1][index],
}))

const stats = statsFixture({
  score_histogram: histogram,
  top_skills: [
    { skill: "Python", count: 8, required: true },
    { skill: "Docker", count: 6, required: false },
    { skill: "PostgreSQL", count: 5, required: true },
  ],
})

const NO_APPLICATIONS = {
  total: 0,
  by_category: { strong_match: 0, moderate_match: 0, weak_match: 0, rejected: 0 },
  by_stage: { unreviewed: 0, screened: 0, phone_call: 0, technical: 0, offer: 0, hired: 0, rejected_manual: 0 },
}

function renderInsights() {
  const onUpload = vi.fn()
  return { ...renderWithProviders(<InsightsTab campaign={campaign} onUpload={onUpload} />), onUpload }
}

/** A chart's panel, by its heading. */
function panel(title: string) {
  return screen.getByRole("region", { name: title })
}

/** The rows of a panel's data table (its text alternative), as cell texts. */
function tableRows(title: string) {
  const table = within(panel(title)).getByRole("table", { hidden: true })
  return within(table)
    .getAllByRole("row", { hidden: true })
    .slice(1)
    .map((row) => within(row).getAllByRole("cell", { hidden: true }).map((cell) => cell.textContent))
}

describe("InsightsTab", () => {
  it("shows a skeleton, then the four charts with summaries", async () => {
    const response = deferred<Response>()
    mockApi({ "GET /campaigns/{id}/stats": () => response.promise })
    renderInsights()

    expect(screen.getByLabelText("Loading insights")).toBeTruthy()
    response.resolve(json(stats))

    expect(await screen.findByRole("heading", { name: "Category distribution" })).toBeTruthy()
    expect(
      within(panel("Category distribution")).getByText(
        "9 applications: 4 strong, 3 possible, 1 unlikely and 1 not a match.",
      ),
    ).toBeTruthy()
    expect(
      within(panel("Score histogram")).getByText("9 scored applications, average score 71. Most score 80–89 (3)."),
    ).toBeTruthy()
    expect(
      within(panel("Top skills")).getByText(
        "The 3 most common skills on the resumes. Required: Python (8), PostgreSQL (5).",
      ),
    ).toBeTruthy()
    expect(within(panel("Processing funnel")).getByText("12 uploaded, 9 processed, 9 scored, 0 reviewed.")).toBeTruthy()
  })

  it("gives every chart a data table as its text alternative", async () => {
    mockApi({ "GET /campaigns/{id}/stats": () => json(stats) })
    renderInsights()
    await screen.findByRole("heading", { name: "Category distribution" })

    expect(tableRows("Category distribution")).toEqual([
      ["Strong match", "4 (44%)"],
      ["Possible match", "3 (33%)"],
      ["Unlikely match", "1 (11%)"],
      ["Not a match", "1 (11%)"],
    ])
    const scores = tableRows("Score histogram")
    expect(scores).toHaveLength(10)
    expect(scores[0]).toEqual(["0–9", "0"])
    expect(scores[8]).toEqual(["80–89", "3"])
    expect(scores[9]).toEqual(["90–100", "1"])
    expect(tableRows("Top skills")).toEqual([
      ["Python", "Yes", "8"],
      ["Docker", "No", "6"],
      ["PostgreSQL", "Yes", "5"],
    ])
    expect(tableRows("Processing funnel")).toEqual([
      ["Uploaded", "12"],
      ["Processed", "9 (75%)"],
      ["Scored", "9 (75%)"],
      ["Reviewed", "0 (0%)"],
      ["Resumes: queued", "1"],
      ["Resumes: scoring", "1"],
      ["Resumes: failed", "1"],
    ])

    // The charts are decorative to assistive technology: the summaries and tables carry the numbers.
    const charts = screen.getAllByTestId("chart")
    expect(charts).toHaveLength(4)
    for (const chart of charts) expect(chart.getAttribute("aria-hidden")).toBe("true")
  })

  it("swaps a chart for its table with the Table toggle", async () => {
    mockApi({ "GET /campaigns/{id}/stats": () => json(stats) })
    renderInsights()
    await screen.findByRole("heading", { name: "Score histogram" })

    const scores = panel("Score histogram")
    const toggle = within(scores).getByRole("button", { name: "Table for score histogram" })
    // The toggle controls the table's wrapper.
    const table = document.getElementById(toggle.getAttribute("aria-controls") ?? "")!
    expect(within(table).getByRole("table", { hidden: true })).toBeTruthy()
    expect(within(scores).getByTestId("chart").getAttribute("aria-hidden")).toBe("true")
    expect(toggle.getAttribute("aria-pressed")).toBe("false")
    expect(table.className).toContain("sr-only")

    fireEvent.click(toggle)

    expect(toggle.getAttribute("aria-pressed")).toBe("true")
    expect(table.className).not.toContain("sr-only")
    expect(within(scores).queryByTestId("chart")).toBeNull()

    fireEvent.click(toggle)
    expect(toggle.getAttribute("aria-pressed")).toBe("false")
    expect(within(scores).getByTestId("chart")).toBeTruthy()
  })

  it("colours the charts from design tokens", async () => {
    mockApi({ "GET /campaigns/{id}/stats": () => json(stats) })
    const { container } = renderInsights()
    await screen.findByRole("heading", { name: "Category distribution" })

    const css = Array.from(container.querySelectorAll("style"), (style) => style.textContent).join("\n")
    expect(css).toContain("--color-strong_match: var(--category-strong);")
    expect(css).toContain("--color-rejected: var(--category-rejected);")
    expect(css).toContain("--color-required: var(--chart-1);")
    expect(css).toContain("--color-other: var(--chart-4);")
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b/i)  })

  it("lists required skills that are not among the most common", async () => {
    mockApi({ "GET /campaigns/{id}/stats": () => json(stats) })
    renderInsights()
    await screen.findByRole("heading", { name: "Top skills" })

    const skills = within(panel("Top skills"))
    expect(skills.getByText(/Required but not among the most common/).textContent).toContain("Kubernetes")
    expect(skills.getByText(/Required but not among the most common/).textContent).not.toContain("Python")
  })

  it("explains each chart that has nothing to plot while resumes are still processing", async () => {
    mockApi({
      "GET /campaigns/{id}/stats": () =>
        json(
          statsFixture({
            resumes: { total: 3, pending: 1, extracting: 1, processing: 1, done: 0, failed: 0, quota_exceeded: 0 },
            applications: NO_APPLICATIONS,
            avg_score: null,
            score_histogram: histogram.map((bucket) => ({ ...bucket, count: 0 })),
          }),
        ),
    })
    renderInsights()
    await screen.findByRole("heading", { name: "Processing funnel" })

    expect(within(panel("Category distribution")).getByText("No scored applications yet.")).toBeTruthy()
    expect(within(panel("Score histogram")).getByText("No scores yet.")).toBeTruthy()
    expect(within(panel("Top skills")).getByText("No skills extracted yet.")).toBeTruthy()
    expect(
      within(panel("Processing funnel")).getByText("Not processed yet: 1 queued, 1 reading, 1 scoring."),
    ).toBeTruthy()
  })

  it("invites uploading when the campaign has no resumes", async () => {
    mockApi({
      "GET /campaigns/{id}/stats": () =>
        json(
          statsFixture({
            resumes: { total: 0, pending: 0, extracting: 0, processing: 0, done: 0, failed: 0, quota_exceeded: 0 },
            applications: NO_APPLICATIONS,
            avg_score: null,
          }),
        ),
    })
    const { onUpload } = renderInsights()

    expect(await screen.findByText("No insights yet")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Upload resumes" }))
    expect(onUpload).toHaveBeenCalledOnce()
  })

  it("shows the problem on error, and retries", async () => {
    let calls = 0
    mockApi({
      "GET /campaigns/{id}/stats": () => {
        calls += 1
        return calls === 1 ? problem(503, "Service Unavailable", "The database is starting.") : json(stats)
      },
    })
    renderInsights()

    expect(await screen.findByText("The database is starting.")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(await screen.findByRole("heading", { name: "Category distribution" })).toBeTruthy()
  })

  it("follows the stats when the workspace's live events invalidate the campaign", async () => {
    let strong = 4
    mockApi({
      "GET /campaigns/{id}/stats": () =>
        json({
          ...stats,
          applications: { ...stats.applications, by_category: { ...stats.applications.by_category, strong_match: strong } },
        }),
    })
    const { queryClient } = renderInsights()
    await screen.findByRole("heading", { name: "Category distribution" })
    expect(tableRows("Category distribution")[0]).toEqual(["Strong match", "4 (44%)"])

    strong = 5
    // What `useCampaignEvents` does when a resume finishes: the stats sit below the campaign's key.
    await queryClient.invalidateQueries({ queryKey: queryKeys.campaigns.detail(CAMPAIGN_ID) })

    await waitFor(() => expect(tableRows("Category distribution")[0]).toEqual(["Strong match", "5 (56%)"]))
  })
})
