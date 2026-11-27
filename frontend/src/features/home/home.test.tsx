import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { Schema } from "@/lib/api/client"
import { makeQueryClient } from "@/lib/query/query-client"
import { deferred, json, problem, renderWithProviders, toApiCall, type ApiCall } from "@/test/helpers"

import { Home } from "./home"
import { HOME_POLL_MS, homeQuery } from "./queries"
import { homeFacts } from "./summary"

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
    client: { id: "00000000-0000-4000-8000-00000000c001", name: "General" },
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
    makeMatch("app-2", { person: { id: "person-2", name: null, email: null }, score: 93 }),
    makeMatch("app-3", {
      person: { id: "person-3", name: "Grace Hopper", email: null },
      pipeline_stage: "phone_call",
      score: 95,
    }),
  ],
}

const EMPTY_HOME: HomeData = { active_campaigns: [], processing: { total: 0, items: [] }, recent_strong_matches: [] }

function stats({
  total = 2,
  active = 2,
  unreviewed = 7,
}: { total?: number; active?: number; unreviewed?: number } = {}): Schema<"Stats"> {
  return {
    campaigns: { total, active, closed: total - active, archived: 0 },
    resumes: { total: 0, pending: 0, extracting: 0, processing: 0, done: 0, failed: 0, quota_exceeded: 0 },
    applications: {
      total: 0,
      by_category: { strong_match: 0, moderate_match: 0, weak_match: 0, rejected: 0 },
      by_stage: { unreviewed, screened: 0, phone_call: 0, technical: 0, offer: 0, hired: 0, rejected_manual: 0 },
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
      if (call.method === "GET" && call.path === "/stats") return (routes.stats ?? (() => json(stats())))()
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
  it("shows a skeleton, then the queue, the open campaigns and the processing line", async () => {
    const response = deferred<Response>()
    installHomeApi({ home: () => response.promise })
    renderHome()

    expect(screen.getByLabelText("Loading home")).toBeTruthy()
    response.resolve(json(HOME))

    expect(await screen.findByRole("region", { name: "Ready for review" })).toBeTruthy()
    expect(section("Open campaigns")).toBeTruthy()
    expect(section("Processing")).toBeTruthy()
    expect(screen.getAllByRole("link", { name: "New campaign" })[0].getAttribute("href")).toBe("/campaigns/new")
  })

  it("shows its title and the day's numbers as one row, then the date", async () => {
    installHomeApi({ home: () => json(HOME) })
    renderHome()

    expect(await screen.findByRole("heading", { level: 1, name: "Home" })).toBeTruthy()
    // 5 + 0 new resumes over the two campaigns Home lists (both of the 2 open ones); the date comes last.
    const today = await screen.findByRole("list", { name: "Today" })
    const items = () => within(today).getAllByRole("listitem").map((item) => item.textContent)
    await waitFor(() => expect(items()).toHaveLength(4))
    expect(items().slice(0, 3)).toEqual(["7 waiting for a first look", "2 open campaigns", "5 new resumes today"])
    expect(items()[3]).toMatch(/^[A-Z][a-z]+day \d{1,2} [A-Z][a-z]+$/)
  })

  it("lists open campaigns as rows with the counts the API gives, linking to each workspace", async () => {
    installHomeApi({ home: () => json(HOME) })
    renderHome()
    const campaigns = await screen.findByRole("region", { name: "Open campaigns" })

    const rows = within(campaigns).getAllByRole("listitem")
    expect(rows).toHaveLength(2)
    const backend = within(rows[0])
    expect(backend.getByRole("link", { name: "Backend hire, Q1" }).getAttribute("href")).toBe(`/campaigns/${BACKEND}`)
    expect(backend.getByText("Backend Engineer")).toBeTruthy()
    expect(backend.getByText("9 applicants · 4 strong · 5 new today · 2 processing · 1 failed")).toBeTruthy()
    expect(within(rows[1]).getByText("3 applicants")).toBeTruthy()
    expect(within(campaigns).getByRole("link", { name: "All campaigns" }).getAttribute("href")).toBe("/campaigns")
  })

  it("says how many resumes are processing, linking each to its campaign's Uploads tab", async () => {
    installHomeApi({ home: () => json(HOME) })
    renderHome()
    const processing = await screen.findByRole("region", { name: "Processing" })

    expect(within(processing).getByText("12 resumes are being read and scored.")).toBeTruthy()
    const items = within(processing).getAllByRole("listitem")
    expect(items).toHaveLength(2)
    expect(within(items[0]).getByText("cv-ada.pdf")).toBeTruthy()
    expect(within(items[0]).getByText("· Scoring")).toBeTruthy()
    expect(within(items[0]).getByRole("link", { name: "Backend hire, Q1: uploads" }).getAttribute("href")).toBe(
      `/campaigns/${BACKEND}?tab=uploads`,
    )
    expect(within(items[1]).getByText("· Queued")).toBeTruthy()
    expect(within(items[1]).getByRole("link", { name: "Data team: uploads" }).getAttribute("href")).toBe(
      `/campaigns/${DATA}?tab=uploads`,
    )
    expect(within(processing).getByText("Showing the 2 newest.")).toBeTruthy()
  })

  it("queues the strong matches nobody has looked at yet, best first, linking to each in their campaign", async () => {
    installHomeApi({ home: () => json(HOME) })
    renderHome()
    const queue = await screen.findByRole("region", { name: "Ready for review" })

    const rows = within(queue).getAllByRole("listitem")
    // Grace Hopper is already at Phone screen: not in the queue.
    expect(rows).toHaveLength(2)
    expect(within(queue).queryByText("Grace Hopper")).toBeNull()
    const unnamed = within(rows[0])
    expect(unnamed.getByRole("link", { name: "Unnamed candidate" }).getAttribute("href")).toBe(`/campaigns/${DATA}?open=app-2`)
    expect(unnamed.getByText("93")).toBeTruthy()
    const ada = within(rows[1])
    expect(ada.getByRole("link", { name: "Ada Lovelace" }).getAttribute("href")).toBe(`/campaigns/${DATA}?open=app-1`)
    expect(ada.getAllByText(/Data team/).length).toBeGreaterThan(0)
    expect(ada.getByText("91")).toBeTruthy()
  })

  it("says so quietly when there is nothing to review or process", async () => {
    installHomeApi({
      home: () => json({ ...HOME, processing: { total: 0, items: [] }, recent_strong_matches: [] }),
    })
    renderHome()

    expect(await screen.findByText("All uploads are processed.")).toBeTruthy()
    expect(screen.getByText(/No strong matches yet/)).toBeTruthy()
    expect(within(section("Open campaigns")).getAllByRole("listitem")).toHaveLength(2)
  })

  it("tells when every recent strong match has been looked at", async () => {
    installHomeApi({
      home: () => json({ ...HOME, recent_strong_matches: [makeMatch("app-9", { pipeline_stage: "screened" })] }),
    })
    renderHome()

    expect(await screen.findByText(/You have looked at every recent strong match/)).toBeTruthy()
  })

  it("invites creating the first campaign on a first run", async () => {
    const calls = installHomeApi({
      home: () => json(EMPTY_HOME),
      stats: () => json(stats({ total: 0, active: 0, unreviewed: 0 })),
    })
    renderHome()

    expect(await screen.findByText("Create your first campaign")).toBeTruthy()
    const links = screen.getAllByRole("link", { name: "New campaign" })
    expect(links).toHaveLength(2)
    expect(links.every((link) => link.getAttribute("href") === "/campaigns/new")).toBe(true)
    expect(screen.queryByRole("region", { name: "Open campaigns" })).toBeNull()
    expect(calls.map((call) => call.path).sort()).toEqual(["/home", "/stats"])
  })

  it("points to the campaign list when every campaign is closed", async () => {
    installHomeApi({ home: () => json(EMPTY_HOME), stats: () => json(stats({ total: 3, active: 0, unreviewed: 0 })) })
    renderHome()

    const campaigns = await screen.findByRole("region", { name: "Open campaigns" })
    expect(within(campaigns).getByText(/No open campaigns/)).toBeTruthy()
    expect(screen.getByText("No campaigns are open")).toBeTruthy()
    expect(screen.queryByText("Create your first campaign")).toBeNull()
  })

  it("still shows the day without its sentence when the counts fail to load", async () => {
    installHomeApi({ home: () => json(HOME), stats: () => problem(503, "Service Unavailable") })
    renderHome()

    expect(await screen.findByRole("region", { name: "Ready for review" })).toBeTruthy()
    expect(screen.queryByText(/waiting for a first look/)).toBeNull()
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
    expect(await screen.findByRole("region", { name: "Ready for review" })).toBeTruthy()
  })
})

describe("homeFacts", () => {
  const home = (newToday: number[]): HomeData => ({
    ...EMPTY_HOME,
    active_campaigns: newToday.map((n, index) =>
      makeCampaign({ id: `c${index}`, counts: { ...makeCampaign().counts, new_resumes_24h: n } }),
    ),
  })
  const text = (facts: ReturnType<typeof homeFacts>) =>
    facts ? facts.map((fact) => (fact.count === null ? fact.label : `${fact.count.toLocaleString("en-GB")} ${fact.label}`)) : null

  it("leads with the candidates waiting for a first look", () => {
    expect(text(homeFacts(home([0, 0]), stats({ active: 2, unreviewed: 1 })))).toEqual([
      "1 waiting for a first look",
      "2 open campaigns",
    ])
    expect(text(homeFacts(home([2, 1]), stats({ active: 1, unreviewed: 1200 })))).toEqual([
      "1,200 waiting for a first look",
      "1 open campaign",
      "3 new resumes today",
    ])
  })

  it("leaves out the new resumes when Home does not list every open campaign", () => {
    expect(text(homeFacts(home([4]), stats({ total: 12, active: 11, unreviewed: 3 })))).toEqual([
      "3 waiting for a first look",
      "11 open campaigns",
    ])
  })

  it("covers a clear desk, no open campaigns and a first run", () => {
    expect(text(homeFacts(home([0]), stats({ active: 1, unreviewed: 0 })))).toEqual([
      "Everyone has had a first look",
      "1 open campaign",
    ])
    expect(text(homeFacts(EMPTY_HOME, stats({ total: 3, active: 0 })))).toEqual(["No campaigns are open"])
    expect(homeFacts(EMPTY_HOME, stats({ total: 0, active: 0 }))).toBeNull()
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
