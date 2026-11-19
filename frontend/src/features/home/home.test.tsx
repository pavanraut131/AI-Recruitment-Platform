import { fireEvent, screen, within } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { Schema } from "@/lib/api/client"
import { makeQueryClient } from "@/lib/query/query-client"
import { deferred, json, problem, renderWithProviders, toApiCall, type ApiCall } from "@/test/helpers"

import { Home } from "./home"
import { HOME_POLL_MS, homeQuery } from "./queries"

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

type HomeData = Schema<"Home">

const BACKEND = "c0000000-0000-4000-8000-000000000001"
const DATA = "c0000000-0000-4000-8000-000000000002"

function makeCampaign(overrides: Partial<Schema<"Campaign">> = {}): Schema<"Campaign"> {
  return {
    id: BACKEND,
    title: "Backend hire, Q1",
    role: "Backend Engineer",
    description: null,
    required_skills: ["Python"],
    status: "active",
    created_at: "2026-11-01T09:00:00Z",
    updated_at: "2026-11-01T09:00:00Z",
    last_activity_at: "2026-11-17T09:00:00Z",
    counts: { resumes: 12, in_progress: 2, failed: 1, new_resumes_24h: 5, applications: 9, strong_matches: 4 },
    ...overrides,
  }
}

function makeProcessing(id: string, overrides: Partial<Schema<"ProcessingResume">> = {}): Schema<"ProcessingResume"> {
  return {
    id,
    campaign_id: BACKEND,
    campaign: { id: BACKEND, title: "Backend hire, Q1", status: "active" },
    filename: `${id}.pdf`,
    size_bytes: 120_000,
    status: "extracting",
    processing_step: null,
    error_message: null,
    retry_count: 0,
    application_id: null,
    uploaded_at: "2026-11-18T09:00:00Z",
    updated_at: "2026-11-18T09:00:00Z",
    ...overrides,
  }
}

function makeMatch(id: string, overrides: Partial<Schema<"RecentMatch">> = {}): Schema<"RecentMatch"> {
  return {
    id,
    campaign: { id: DATA, title: "Data team", status: "active" },
    person: { id: `person-${id}`, name: "Ada Lovelace", email: "ada@example.com" },
    score: 91,
    category: "strong_match",
    pipeline_stage: null,
    created_at: "2026-11-18T08:00:00Z",
    ...overrides,
  }
}

const HOME: HomeData = {
  active_campaigns: [
    makeCampaign(),
    makeCampaign({
      id: DATA,
      title: "Data team",
      role: "Data Engineer",
      counts: { resumes: 3, in_progress: 0, failed: 0, new_resumes_24h: 0, applications: 3, strong_matches: 0 },
    }),
  ],
  processing: {
    total: 12,
    items: [
      makeProcessing("cv-ada", { status: "processing" }),
      makeProcessing("cv-bob", { status: "pending", campaign_id: DATA, campaign: { id: DATA, title: "Data team", status: "active" } }),
    ],
  },
  recent_strong_matches: [
    makeMatch("app-1"),
    makeMatch("app-2", { person: { id: "person-2", name: null, email: null }, pipeline_stage: "phone_call", score: 88 }),
  ],
}

const EMPTY_HOME: HomeData = { active_campaigns: [], processing: { total: 0, items: [] }, recent_strong_matches: [] }

function stats(campaigns: number): Schema<"Stats"> {
  return {
    campaigns: { total: campaigns, active: 0, closed: campaigns, archived: 0 },
    resumes: { total: 0, pending: 0, extracting: 0, processing: 0, done: 0, failed: 0, quota_exceeded: 0 },
    applications: {
      total: 0,
      by_category: { strong_match: 0, moderate_match: 0, weak_match: 0, rejected: 0 },
      by_stage: { unreviewed: 0, screened: 0, phone_call: 0, technical: 0, offer: 0, hired: 0, rejected_manual: 0 },
    },
    people: 0,
  }
}

/** A fake `/api/v2` for Home: `GET /home` and `GET /stats`. */
function installHomeApi(routes: { home: () => Response | Promise<Response>; stats?: () => Response }) {
  const calls: ApiCall[] = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const call = await toApiCall(request)
      calls.push(call)
      if (call.method === "GET" && call.path === "/home") return routes.home()
      if (call.method === "GET" && call.path === "/stats" && routes.stats) return routes.stats()
      return problem(404, "Not Found")
    }),
  )
  return calls
}

function renderHome() {
  const queryClient = makeQueryClient()
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } })
  return renderWithProviders(<Home />, { queryClient })
}

function section(name: string) {
  return screen.getByRole("region", { name })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("Home", () => {
  it("shows a skeleton, then the three sections", async () => {
    const response = deferred<Response>()
    installHomeApi({ home: () => response.promise })
    renderHome()

    expect(screen.getByLabelText("Loading home")).toBeTruthy()
    response.resolve(json(HOME))

    expect(await screen.findByRole("region", { name: "Active campaigns" })).toBeTruthy()
    expect(section("Recent strong matches")).toBeTruthy()
    expect(section("Processing")).toBeTruthy()
    expect(screen.getAllByRole("link", { name: "New campaign" })[0].getAttribute("href")).toBe("/campaigns/new")
  })

  it("lists active campaigns with their status, new uploads, strong matches and processing, linking to each workspace", async () => {
    installHomeApi({ home: () => json(HOME) })
    renderHome()
    const campaigns = await screen.findByRole("region", { name: "Active campaigns" })

    const cards = within(campaigns).getAllByRole("article")
    expect(cards).toHaveLength(2)
    const backend = within(cards[0])
    expect(backend.getByRole("link", { name: "Backend hire, Q1" }).getAttribute("href")).toBe(`/campaigns/${BACKEND}`)
    expect(backend.getByText("Active")).toBeTruthy()
    const count = (label: string) => backend.getByText(label, { selector: "dt" }).nextElementSibling?.textContent
    expect(count("New in 24 h")).toBe("5")
    expect(count("Strong matches")).toBe("4")
    expect(count("Processing")).toBe("21 failed")
    expect(within(cards[1]).getByRole("link", { name: "Data team" }).getAttribute("href")).toBe(`/campaigns/${DATA}`)
    expect(within(campaigns).getByRole("link", { name: "All campaigns" }).getAttribute("href")).toBe("/campaigns")
  })

  it("lists uploads still processing, linking to each campaign's Uploads tab", async () => {
    installHomeApi({ home: () => json(HOME) })
    renderHome()
    const processing = await screen.findByRole("region", { name: "Processing" })

    expect(within(processing).getByText("12 resumes in progress.")).toBeTruthy()
    const items = within(processing).getAllByRole("listitem")
    expect(items).toHaveLength(2)
    expect(within(items[0]).getByText("cv-ada.pdf")).toBeTruthy()
    expect(within(items[0]).getByText("Analysing")).toBeTruthy()
    expect(within(items[0]).getByRole("link", { name: "Backend hire, Q1: uploads" }).getAttribute("href")).toBe(
      `/campaigns/${BACKEND}?tab=uploads`,
    )
    expect(within(items[1]).getByText("Queued")).toBeTruthy()
    expect(within(items[1]).getByRole("link", { name: "Data team: uploads" }).getAttribute("href")).toBe(
      `/campaigns/${DATA}?tab=uploads`,
    )
    expect(within(processing).getByText("Showing the 2 newest of 12.")).toBeTruthy()
  })

  it("lists recent strong matches, linking to the candidate in their campaign", async () => {
    installHomeApi({ home: () => json(HOME) })
    renderHome()
    const matches = await screen.findByRole("region", { name: "Recent strong matches" })

    const rows = within(matches).getAllByRole("row").slice(1)
    expect(rows).toHaveLength(2)
    const ada = within(rows[0])
    expect(ada.getByRole("link", { name: "Ada Lovelace" }).getAttribute("href")).toBe(`/campaigns/${DATA}?open=app-1`)
    expect(ada.getByText("Data team")).toBeTruthy()
    expect(ada.getByText("91")).toBeTruthy()
    expect(ada.getByText("Unreviewed")).toBeTruthy()
    const unnamed = within(rows[1])
    expect(unnamed.getByRole("link", { name: "Unnamed candidate" }).getAttribute("href")).toBe(
      `/campaigns/${DATA}?open=app-2`,
    )
    expect(unnamed.getByText("Phone call")).toBeTruthy()
  })

  it("says so quietly when a section is empty", async () => {
    installHomeApi({
      home: () => json({ ...HOME, processing: { total: 0, items: [] }, recent_strong_matches: [] }),
    })
    renderHome()

    expect(await screen.findByText("Nothing is processing. Every upload is done.")).toBeTruthy()
    expect(screen.getByText(/No strong matches yet/)).toBeTruthy()
    expect(within(section("Active campaigns")).getAllByRole("article")).toHaveLength(2)
  })

  it("invites creating the first campaign on a first run", async () => {
    const calls = installHomeApi({ home: () => json(EMPTY_HOME), stats: () => json(stats(0)) })
    renderHome()

    expect(await screen.findByText("Create your first campaign")).toBeTruthy()
    const links = screen.getAllByRole("link", { name: "New campaign" })
    expect(links).toHaveLength(2)
    expect(links.every((link) => link.getAttribute("href") === "/campaigns/new")).toBe(true)
    expect(screen.queryByRole("region", { name: "Active campaigns" })).toBeNull()
    expect(calls.map((call) => call.path)).toEqual(["/home", "/stats"])
  })

  it("points to the campaign list when every campaign is closed", async () => {
    installHomeApi({ home: () => json(EMPTY_HOME), stats: () => json(stats(3)) })
    renderHome()

    const campaigns = await screen.findByRole("region", { name: "Active campaigns" })
    expect(within(campaigns).getByText(/No active campaigns/)).toBeTruthy()
    expect(screen.queryByText("Create your first campaign")).toBeNull()
  })

  it("only asks for the workspace counts when there is nothing to show", async () => {
    const calls = installHomeApi({ home: () => json(HOME) })
    renderHome()
    await screen.findByRole("region", { name: "Active campaigns" })

    expect(calls.map((call) => call.path)).toEqual(["/home"])
  })

  it("shows the problem on error, and retries", async () => {
    let calls = 0
    installHomeApi({
      home: () => {
        calls += 1
        return calls === 1 ? problem(503, "Service Unavailable", "The database is starting.") : json(HOME)
      },
    })
    renderHome()

    expect(await screen.findByText("The database is starting.")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(await screen.findByRole("region", { name: "Active campaigns" })).toBeTruthy()
  })
})

describe("homeQuery", () => {
  it("refetches while resumes are processing, and stops when none are", () => {
    const interval = homeQuery().refetchInterval as (query: { state: { data?: HomeData } }) => number | false
    expect(interval({ state: { data: HOME } })).toBe(HOME_POLL_MS)
    expect(interval({ state: { data: EMPTY_HOME } })).toBe(false)
    expect(interval({ state: {} })).toBe(false)
  })
})
