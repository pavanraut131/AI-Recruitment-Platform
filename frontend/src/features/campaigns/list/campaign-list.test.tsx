import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import type { UrlUpdateEvent } from "nuqs/adapters/testing"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { meAs } from "@/test/auth"
import { installDomPolyfills } from "@/test/helpers"

import { campaignFixture, clientFixture, deferred, GENERAL_CLIENT, json, mockApi, pageOf, problem, renderWithProviders } from "../test-utils"
import { CampaignList } from "./campaign-list"

const { push } = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

beforeEach(() => {
  push.mockClear()
})

const ACTIVE = campaignFixture()
const CLOSED = campaignFixture({
  id: "33333333-3333-4333-8333-333333333333",
  title: "Data team",
  role: "Data Engineer",
  status: "closed",
  counts: { resumes: 0, in_progress: 0, failed: 0, new_resumes_24h: 0, applications: 0, strong_matches: 0 },
})

function renderList(searchParams = "", { me }: { me?: ReturnType<typeof meAs> } = {}) {
  const urlUpdates: UrlUpdateEvent[] = []
  const view = renderWithProviders(<CampaignList />, {
    searchParams,
    onUrlUpdate: (event) => urlUpdates.push(event),
    me,
  })
  return { ...view, urlUpdates, lastUrl: () => urlUpdates.at(-1)?.queryString }
}

/** The campaign list's requests (the client picker's `GET /clients` aside). */
function requestedUrls(fetchMock: ReturnType<typeof mockApi>) {
  return fetchMock.mock.calls
    .map(([request]) => new URL(request.url))
    .filter((url) => url.pathname.endsWith("/campaigns"))
}

describe("CampaignList", () => {
  it("shows a skeleton while loading, then the table", async () => {
    const response = deferred<Response>()
    mockApi({ "GET /campaigns": () => response.promise })
    renderList()

    expect(screen.getByLabelText("Loading campaigns")).toBeTruthy()

    response.resolve(json(pageOf([ACTIVE, CLOSED])))

    const table = await screen.findByRole("table")
    const rows = within(table).getAllByRole("row")
    expect(rows).toHaveLength(3) // header + 2
    const first = within(rows[1])
    expect(first.getByRole("link", { name: ACTIVE.title }).getAttribute("href")).toBe(`/campaigns/${ACTIVE.id}`)
    expect(first.getByText("Backend Engineer")).toBeTruthy()
    expect(first.getByText("Active")).toBeTruthy()
    expect(first.getByText("9")).toBeTruthy() // applications
    expect(first.getByText("4")).toBeTruthy() // strong matches
    expect(first.getByText("1 failed")).toBeTruthy()
    expect(within(rows[2]).getByText("Closed")).toBeTruthy()
    expect(screen.getByText("1–2 of 2 campaigns")).toBeTruthy()
  })

  it("opens a campaign when its row is clicked", async () => {
    mockApi({ "GET /campaigns": () => json(pageOf([ACTIVE])) })
    renderList()

    fireEvent.click(await screen.findByText("Backend Engineer"))

    expect(push).toHaveBeenCalledWith(`/campaigns/${ACTIVE.id}`)
  })

  it("reads the status filter, search and page from the URL", async () => {
    const fetchMock = mockApi({ "GET /campaigns": () => json(pageOf([CLOSED], { total: 21, page: 2 })) })
    renderList("?status=archived,closed&q=data&page=2")

    await screen.findByRole("table")
    const url = requestedUrls(fetchMock)[0]
    expect(url.searchParams.getAll("status")).toEqual(["closed", "archived"])
    expect(url.searchParams.get("q")).toBe("data")
    expect(url.searchParams.get("page")).toBe("2")
    expect(url.searchParams.get("page_size")).toBe("20")

    expect(screen.getByRole("searchbox", { name: /search campaigns/i })).toHaveProperty("value", "data")
    const filters = screen.getByRole("group", { name: "Filter by status" })
    expect(within(filters).getByRole("button", { name: "Closed" }).getAttribute("aria-pressed")).toBe("true")
    expect(within(filters).getByRole("button", { name: "Archived" }).getAttribute("aria-pressed")).toBe("true")
    expect(within(filters).getByRole("button", { name: "Active" }).getAttribute("aria-pressed")).toBe("false")
    expect(screen.getByText("21 of 21 campaigns")).toBeTruthy()
  })

  it("writes status filters to the URL and goes back to the first page", async () => {
    const fetchMock = mockApi({ "GET /campaigns": () => json(pageOf([ACTIVE])) })
    const view = renderList("?page=3")
    await screen.findByRole("table")

    fireEvent.click(screen.getByRole("button", { name: "Active" }))
    await waitFor(() => expect(view.lastUrl()).toBe("?status=active"))

    fireEvent.click(screen.getByRole("button", { name: "Closed" }))
    await waitFor(() => expect(view.lastUrl()).toBe("?status=active,closed"))
    await waitFor(() =>
      expect(requestedUrls(fetchMock).at(-1)?.searchParams.getAll("status")).toEqual(["active", "closed"]),
    )

    fireEvent.click(screen.getByRole("button", { name: "Active" }))
    await waitFor(() => expect(view.lastUrl()).toBe("?status=closed"))
  })

  it("writes the search to the URL (debounced) and fetches with it", async () => {
    const fetchMock = mockApi({ "GET /campaigns": () => json(pageOf([ACTIVE])) })
    const view = renderList()
    await screen.findByRole("table")

    fireEvent.change(screen.getByRole("searchbox", { name: /search campaigns/i }), { target: { value: "back" } })

    await waitFor(() => expect(view.lastUrl()).toBe("?q=back"))
    await waitFor(() => expect(requestedUrls(fetchMock).at(-1)?.searchParams.get("q")).toBe("back"))
    // One request for the first load, one for the search: not one per keystroke.
    expect(requestedUrls(fetchMock)).toHaveLength(2)
  })

  it("invites creating the first campaign when there are none", async () => {
    mockApi({ "GET /campaigns": () => json(pageOf([])) })
    renderList()

    expect(await screen.findByText("No campaigns yet")).toBeTruthy()
    const links = screen.getAllByRole("link", { name: "New campaign" })
    expect(links.every((link) => link.getAttribute("href") === "/campaigns/new")).toBe(true)
  })

  it("offers to clear the filters when nothing matches", async () => {
    mockApi({ "GET /campaigns": () => json(pageOf([])) })
    const view = renderList("?status=archived&q=zzz")

    expect(await screen.findByText("No campaigns match")).toBeTruthy()
    // The empty state's button (the toolbar has one too).
    fireEvent.click(screen.getAllByRole("button", { name: "Clear filters" }).at(-1)!)

    await waitFor(() => expect(view.lastUrl()).toBe(""))
  })

  it("shows the problem detail on error, and retries", async () => {
    let calls = 0
    mockApi({
      "GET /campaigns": () => {
        calls += 1
        return calls === 1 ? problem(503, "Service Unavailable", "The database is starting.") : json(pageOf([ACTIVE]))
      },
    })
    renderList()

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("Service Unavailable")
    expect(alert.textContent).toContain("The database is starting.")

    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }))

    expect(await screen.findByRole("table")).toBeTruthy()
  })
})

describe("CampaignList: clients and roles (E2-10)", () => {
  const ACME = clientFixture({ id: "00000000-0000-4000-8000-00000000c002", name: "Acme Corp" })
  const OLD = clientFixture({ id: "00000000-0000-4000-8000-00000000c003", name: "Old Co", status: "archived" })

  beforeEach(() => {
    installDomPolyfills()
    window.localStorage.clear()
  })

  it("shows each campaign's client in its own column", async () => {
    mockApi({ "GET /campaigns": () => json(pageOf([campaignFixture({ client: { id: ACME.id, name: ACME.name } })])) })
    renderList()

    const table = await screen.findByRole("table")
    expect(within(table).getByRole("columnheader", { name: "Client" })).toBeTruthy()
    expect(within(within(table).getAllByRole("row")[1]).getByText("Acme Corp")).toBeTruthy()
  })

  it("filters by the client in the URL, offers the active clients, and starts a new campaign with it", async () => {
    const fetchMock = mockApi({
      "GET /clients": () => json(pageOf([ACME, GENERAL_CLIENT, OLD])),
      "GET /campaigns": () => json(pageOf([ACTIVE])),
    })
    const view = renderList(`?client=${ACME.id}&page=2`)

    await screen.findByRole("table")
    expect(requestedUrls(fetchMock)[0].searchParams.getAll("client_id")).toEqual([ACME.id])
    expect(screen.getByRole("link", { name: "New campaign" }).getAttribute("href")).toBe(`/campaigns/new?client=${ACME.id}`)

    const filter = screen.getByRole("combobox", { name: "Filter by client" })
    await waitFor(() => expect(filter.textContent).toBe("Acme Corp"))
    fireEvent.click(filter)
    const options = (await screen.findAllByRole("option")).map((option) => option.textContent)
    // Archived clients are not offered.
    expect(options).toEqual(["All clients", "Acme Corp", "General"])
    fireEvent.click(screen.getByRole("option", { name: "General" }))

    // A new client starts from the first page, and is remembered for the agency.
    await waitFor(() => expect(view.lastUrl()).toBe(`?client=${GENERAL_CLIENT.id}`))
    expect(window.localStorage.getItem(`hireflow.client.${meAs("owner").active_agency!.id}`)).toBe(GENERAL_CLIENT.id)
    await waitFor(() =>
      expect(requestedUrls(fetchMock).at(-1)?.searchParams.getAll("client_id")).toEqual([GENERAL_CLIENT.id]),
    )
  })

  it("says when a chosen client has no campaigns, and goes back to all clients", async () => {
    mockApi({
      "GET /clients": () => json(pageOf([ACME, GENERAL_CLIENT])),
      "GET /campaigns": () => json(pageOf([])),
    })
    const view = renderList(`?client=${ACME.id}`)

    expect(await screen.findByText("No campaigns for Acme Corp yet")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Show all clients" }))
    await waitFor(() => expect(view.lastUrl()).toBe(""))
  })

  it("offers a viewer no New campaign, even with no campaigns at all", async () => {
    mockApi({ "GET /campaigns": () => json(pageOf([])) })
    renderList("", { me: meAs("viewer") })

    expect(await screen.findByText("No campaigns yet")).toBeTruthy()
    expect(screen.getByText("Campaigns your agency creates show up here.")).toBeTruthy()
    expect(screen.queryByRole("link", { name: "New campaign" })).toBeNull()
  })
})
