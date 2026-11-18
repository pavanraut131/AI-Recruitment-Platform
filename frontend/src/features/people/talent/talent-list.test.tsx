import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import type { UrlUpdateEvent } from "nuqs/adapters/testing"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { Schema } from "@/lib/api/client"
import { makeQueryClient } from "@/lib/query/query-client"
import { deferred, json, problem, renderWithProviders, toApiCall } from "@/test/helpers"

import { TalentList } from "./talent-list"

const { push } = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

beforeEach(() => {
  push.mockClear()
})

// The search waits 300 ms after the last key press: real timers, so give these tests room.
const SEARCH_TEST_TIMEOUT_MS = 10_000

type PersonListItem = Schema<"PersonListItem">

function makeListItem(overrides: Partial<PersonListItem> = {}): PersonListItem {
  return {
    id: "aaaaaaaa-0000-4000-8000-000000000001",
    name: "Ada Lovelace",
    email: "ada@example.com",
    latest_role: "Staff engineer",
    skills: ["Python", "React", "TypeScript", "SQL", "Rust"],
    years_of_experience: 7,
    application_count: 3,
    last_applied_at: "2026-11-15T09:00:00Z",
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-11-15T09:00:00Z",
    ...overrides,
  }
}

const ADA = makeListItem()
const GRACE = makeListItem({
  id: "aaaaaaaa-0000-4000-8000-000000000002",
  name: null,
  email: null,
  latest_role: null,
  skills: [],
  application_count: 0,
  last_applied_at: null,
})

function pageOf(items: PersonListItem[], { total = items.length, page = 1, page_size = 20 } = {}) {
  return { items, total, page, page_size }
}

/** A fake `GET /people` answering with `respond(url)`; returns the requested URLs. */
function installPeopleList(respond: (url: URL) => Response | Promise<Response>) {
  const urls: URL[] = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const call = await toApiCall(request)
      if (call.method !== "GET" || call.path !== "/people") return problem(404, "Not Found")
      urls.push(call.url)
      return respond(call.url)
    }),
  )
  return urls
}

function renderTalent(searchParams = "") {
  const urlUpdates: UrlUpdateEvent[] = []
  const queryClient = makeQueryClient()
  // A failed load shows its error at once (the app retries 5xx in the background).
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } })
  const view = renderWithProviders(<TalentList />, {
    searchParams,
    onUrlUpdate: (event) => urlUpdates.push(event),
    queryClient,
  })
  return { ...view, lastUrl: () => urlUpdates.at(-1)?.queryString }
}

describe("TalentList", () => {
  it("shows a skeleton, then a row per person linking to their profile", async () => {
    const response = deferred<Response>()
    installPeopleList(() => response.promise)
    renderTalent()

    expect(screen.getByLabelText("Loading people")).toBeTruthy()
    response.resolve(json(pageOf([ADA, GRACE])))

    const table = await screen.findByRole("table")
    const [header, ada, grace] = within(table).getAllByRole("row")
    expect(within(header).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
      "Name",
      "Latest role",
      "Skills",
      "Applications",
      "Last seen",
    ])

    const first = within(ada)
    expect(first.getByRole("link", { name: "Ada Lovelace" }).getAttribute("href")).toBe(`/people/${ADA.id}`)
    expect(first.getByText("ada@example.com")).toBeTruthy()
    expect(first.getByText("Staff engineer")).toBeTruthy()
    const skills = first.getByRole("list", { name: "Skills" })
    expect(within(skills).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Python",
      "React",
      "TypeScript",
      "+2 more: SQL, Rust",
    ])
    expect(first.getByText("3")).toBeTruthy()
    expect(ada.querySelector("time")?.getAttribute("dateTime")).toBe(ADA.last_applied_at)

    // A person without a name, role, skills or applications.
    const second = within(grace)
    expect(second.getByRole("link", { name: "Unnamed candidate" }).getAttribute("href")).toBe(`/people/${GRACE.id}`)
    expect(grace.querySelector("time")).toBeNull()

    expect(screen.getByText("1–2 of 2 people")).toBeTruthy()
  })

  it("opens a profile when its row is clicked", async () => {
    installPeopleList(() => json(pageOf([ADA])))
    renderTalent()

    fireEvent.click(await screen.findByText("Staff engineer"))

    expect(push).toHaveBeenCalledWith(`/people/${ADA.id}`)
  })

  it("reads the search and page from the URL", async () => {
    const urls = installPeopleList(() => json(pageOf([ADA], { total: 41, page: 3 })))
    renderTalent("?q=ada&page=3")

    await screen.findByRole("table")
    expect(urls[0].searchParams.get("q")).toBe("ada")
    expect(urls[0].searchParams.get("page")).toBe("3")
    expect(urls[0].searchParams.get("page_size")).toBe("20")
    expect(screen.getByRole("searchbox", { name: "Search people by name or email" })).toHaveProperty("value", "ada")
    expect(screen.getByText("41 of 41 people")).toBeTruthy()
  })

  it(
    "writes the search to the URL (debounced), back on the first page",
    async () => {
      const urls = installPeopleList(() => json(pageOf([ADA], { total: 45 })))
      const view = renderTalent("?page=2")
      await screen.findByRole("table")

      const search = screen.getByRole("searchbox", { name: "Search people by name or email" })
      fireEvent.change(search, { target: { value: "a" } })
      fireEvent.change(search, { target: { value: "ada@" } })

      await waitFor(() => expect(view.lastUrl()).toBe("?q=ada@"))
      await waitFor(() => expect(urls.at(-1)?.searchParams.get("q")).toBe("ada@"))
      expect(urls.at(-1)?.searchParams.get("page")).toBe("1")
      // One request for the first load, one for the search: not one per keystroke.
      expect(urls).toHaveLength(2)
    },
    SEARCH_TEST_TIMEOUT_MS,
  )

  it("pages with Previous and Next, keeping the page in the URL", async () => {
    const urls = installPeopleList((url) => {
      const page = Number(url.searchParams.get("page"))
      return json(pageOf([page === 1 ? ADA : GRACE], { total: 21, page }))
    })
    const view = renderTalent()
    await screen.findByRole("table")

    const pagination = screen.getByRole("navigation", { name: "Pagination" })
    expect(within(pagination).getByRole("button", { name: "Previous" })).toHaveProperty("disabled", true)

    fireEvent.click(within(pagination).getByRole("button", { name: "Next" }))

    await waitFor(() => expect(view.lastUrl()).toBe("?page=2"))
    expect(await screen.findByRole("link", { name: "Unnamed candidate" })).toBeTruthy()
    expect(urls.at(-1)?.searchParams.get("page")).toBe("2")
    expect(screen.getByText("21 of 21 people")).toBeTruthy()
    expect(within(pagination).getByRole("button", { name: "Next" })).toHaveProperty("disabled", true)

    fireEvent.click(within(pagination).getByRole("button", { name: "Previous" }))
    await waitFor(() => expect(view.lastUrl()).toBe(""))
  })

  it("says when no one has applied yet", async () => {
    installPeopleList(() => json(pageOf([])))
    renderTalent()

    expect(await screen.findByText("No people yet")).toBeTruthy()
    expect(screen.getByRole("link", { name: "Go to campaigns" }).getAttribute("href")).toBe("/campaigns")
  })

  it("offers to clear a search that matches no one", async () => {
    installPeopleList(() => json(pageOf([])))
    const view = renderTalent("?q=zzz")

    expect(await screen.findByText("No one matches")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }))
    await waitFor(() => expect(view.lastUrl()).toBe(""))
  })

  it("offers the first page when the page in the URL is past the end", async () => {
    installPeopleList(() => json(pageOf([], { total: 5, page: 9 })))
    const view = renderTalent("?page=9")

    expect(await screen.findByText("No people on this page")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Go to the first page" }))
    await waitFor(() => expect(view.lastUrl()).toBe(""))
  })

  it("shows the problem on error, and retries", async () => {
    let calls = 0
    installPeopleList(() => {
      calls += 1
      return calls === 1 ? problem(503, "Service Unavailable", "The database is starting.") : json(pageOf([ADA]))
    })
    renderTalent()

    expect(await screen.findByText("The database is starting.")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(await screen.findByRole("link", { name: "Ada Lovelace" })).toBeTruthy()
  })
})
