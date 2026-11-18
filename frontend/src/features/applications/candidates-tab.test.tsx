import { act, fireEvent, screen, waitFor, within } from "@testing-library/react"
import type { UrlUpdateEvent } from "nuqs/adapters/testing"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { CandidatesTab } from "./candidates-tab"
import { makeApplication, makeApplications } from "./fixtures"
import type { Application } from "./model"
import {
  CAMPAIGN_ID,
  LIST_PATH,
  deferred,
  installDomPolyfills,
  installFakeApi,
  json,
  problem,
  renderWithProviders,
} from "./test-utils"

const { toast } = vi.hoisted(() => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}))
vi.mock("sonner", () => ({ toast }))

beforeEach(() => {
  installDomPolyfills()
  toast.success.mockClear()
  toast.warning.mockClear()
  toast.error.mockClear()
})

const people: Application[] = [
  makeApplication(0, { person: { id: "p0", name: "Ada Lovelace", email: "ada@example.com" }, score: 91, category: "strong_match", pipeline_stage: null }),
  makeApplication(1, { person: { id: "p1", name: "Grace Hopper", email: "grace@example.com" }, score: 74, category: "moderate_match", pipeline_stage: "screened" }),
  makeApplication(2, { person: { id: "p2", name: "Alan Turing", email: "alan@example.com" }, score: 40, category: "weak_match", pipeline_stage: null }),
]

function renderTab(searchParams = "") {
  const onUrlUpdate = vi.fn<(event: UrlUpdateEvent) => void>()
  const result = renderWithProviders(<CandidatesTab campaignId={CAMPAIGN_ID} />, { searchParams, onUrlUpdate })
  const lastQuery = () => onUrlUpdate.mock.lastCall?.[0].queryString
  return { ...result, onUrlUpdate, lastQuery }
}

/** The query string of the latest list request. */
function lastListQuery(api: ReturnType<typeof installFakeApi>): URLSearchParams {
  const call = api.callsTo("GET", LIST_PATH).at(-1)
  if (!call) throw new Error("no list request")
  return call.url.searchParams
}

const table = () => document.querySelector<HTMLElement>("[data-slot='candidates-table']")!
const rowOf = (name: string) => within(table()).getByText(name).closest("tr") as HTMLTableRowElement
const stageOf = (name: string) => rowOf(name).querySelector("td[data-column='stage']")?.textContent

/** Open a Radix dropdown from the keyboard (jsdom has no real pointer events). */
function openMenu(name: string | RegExp) {
  const trigger = screen.getByRole("button", { name })
  fireEvent.keyDown(trigger, { key: "Enter" })
  return screen.findByRole("menu")
}

describe("CandidatesTab: loading and states", () => {
  it("shows skeleton rows, then the first page sorted by score", async () => {
    const api = installFakeApi({ applications: people })
    renderTab()
    expect(document.querySelectorAll("[data-slot='skeleton-row']").length).toBeGreaterThan(0)
    expect(await screen.findByText("Ada Lovelace")).toBeTruthy()
    const query = lastListQuery(api)
    expect(query.get("sort")).toBe("-score")
    expect(query.get("page")).toBe("1")
    expect(query.get("page_size")).toBe("50")
    expect(screen.getByText("1–3 of 3")).toBeTruthy()
  })

  it("invites an upload when the campaign has no candidates", async () => {
    installFakeApi({ applications: [] })
    renderTab()
    expect(await screen.findByText("No candidates yet")).toBeTruthy()
    expect(screen.getByRole("link", { name: "Upload resumes" }).getAttribute("href")).toBe("?tab=uploads")
    expect(screen.queryByRole("search")).toBeNull()
  })

  it("offers to clear the filters when they match nothing", async () => {
    installFakeApi({ applications: [] })
    const { lastQuery } = renderTab("?cat=rejected&min=90")
    expect(await screen.findByText("No candidates match these filters")).toBeTruthy()
    const clearButtons = screen.getAllByRole("button", { name: /Clear filters/ })
    fireEvent.click(clearButtons.at(-1)!)
    await waitFor(() => expect(lastQuery()).toBe(""))
  })

  it("shows the problem and retries", async () => {
    let fail = true
    installFakeApi({
      applications: people,
      override: ({ method, path }) =>
        fail && method === "GET" && LIST_PATH.test(path) ? problem(404, "Not Found", "Campaign not found") : undefined,
    })
    renderTab()
    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("Could not load candidates: Not Found")
    expect(alert.textContent).toContain("Campaign not found")
    fail = false
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }))
    expect(await screen.findByText("Ada Lovelace")).toBeTruthy()
  })
})

describe("CandidatesTab: filters, sort and pages in the URL", () => {
  it("requests what a reloaded URL describes and shows it in the toolbar", async () => {
    const api = installFakeApi({ applications: people })
    renderTab("?q=ada&cat=strong,moderate&min=60&max=95&skill=react&stage=screened&sort=name&page=1&size=25")
    await screen.findByText("Ada Lovelace")
    const query = lastListQuery(api)
    expect(query.get("q")).toBe("ada")
    expect(query.getAll("category")).toEqual(["strong_match", "moderate_match"])
    expect(query.get("min_score")).toBe("60")
    expect(query.get("max_score")).toBe("95")
    expect(query.get("skill")).toBe("react")
    expect(query.getAll("stage")).toEqual(["screened"])
    expect(query.get("sort")).toBe("name")
    expect(query.get("page_size")).toBe("25")
    expect((screen.getByRole("searchbox", { name: /Search candidates/ }) as HTMLInputElement).value).toBe("ada")
    expect((screen.getByRole("textbox", { name: "Filter by skill" }) as HTMLInputElement).value).toBe("react")
    expect(screen.getByRole("button", { name: /Category · 2 selected/ })).toBeTruthy()
    expect(screen.getByRole("button", { name: /Score · 60–95/ })).toBeTruthy()
    expect(screen.getByRole("button", { name: /Stage · Screened/ })).toBeTruthy()
  })

  it("debounces the search into the URL and goes back to page 1", async () => {
    const api = installFakeApi({ applications: makeApplications(120) })
    const { lastQuery } = renderTab("?page=2")
    await screen.findByText("51–100 of 120")
    const search = screen.getByRole("searchbox", { name: /Search candidates/ })
    fireEvent.change(search, { target: { value: "gr" } })
    fireEvent.change(search, { target: { value: "grace" } })
    expect(lastQuery()).toBeUndefined()
    await waitFor(() => expect(lastQuery()).toBe("?q=grace"))
    await waitFor(() => expect(lastListQuery(api).get("q")).toBe("grace"))
    expect(lastListQuery(api).get("page")).toBe("1")
    // One request for the committed value, none per keystroke.
    expect(api.callsTo("GET", LIST_PATH).filter((call) => call.url.searchParams.get("q") === "gr")).toHaveLength(0)
  })

  it("filters by category from the multi-select", async () => {
    const api = installFakeApi({ applications: people })
    const { lastQuery } = renderTab()
    await screen.findByText("Ada Lovelace")
    const menu = await openMenu(/^Category/)
    fireEvent.click(within(menu).getByRole("menuitemcheckbox", { name: "Moderate match" }))
    await waitFor(() => expect(lastQuery()).toBe("?cat=moderate"))
    fireEvent.click(within(menu).getByRole("menuitemcheckbox", { name: "Strong match" }))
    // Kept in display order, whatever the click order.
    await waitFor(() => expect(lastQuery()).toBe("?cat=strong,moderate"))
    await waitFor(() => expect(lastListQuery(api).getAll("category")).toEqual(["strong_match", "moderate_match"]))
  })

  it("filters by stage", async () => {
    installFakeApi({ applications: people })
    const { lastQuery } = renderTab()
    await screen.findByText("Ada Lovelace")
    const menu = await openMenu(/^Stage/)
    fireEvent.click(within(menu).getByRole("menuitemcheckbox", { name: "Offer" }))
    await waitFor(() => expect(lastQuery()).toBe("?stage=offer"))
  })

  it("applies a score range (and swaps an inverted one)", async () => {
    const api = installFakeApi({ applications: people })
    const { lastQuery } = renderTab()
    await screen.findByText("Ada Lovelace")
    fireEvent.click(screen.getByRole("button", { name: /^Score/ }))
    fireEvent.change(await screen.findByLabelText("Min score"), { target: { value: "80" } })
    fireEvent.change(screen.getByLabelText("Max score"), { target: { value: "55" } })
    fireEvent.click(screen.getByRole("button", { name: "Apply" }))
    await waitFor(() => expect(lastQuery()).toBe("?min=55&max=80"))
    await waitFor(() => expect(lastListQuery(api).get("min_score")).toBe("55"))
  })

  it("filters by skill and clears everything", async () => {
    installFakeApi({ applications: people })
    const { lastQuery } = renderTab("?sort=score&cat=weak")
    await screen.findByText("Ada Lovelace")
    const skill = screen.getByRole("textbox", { name: "Filter by skill" })
    fireEvent.change(skill, { target: { value: "rust" } })
    fireEvent.keyDown(skill, { key: "Enter" })
    await waitFor(() => expect(lastQuery()).toBe("?sort=score&cat=weak&skill=rust"))
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }))
    await waitFor(() => expect(lastQuery()).toBe("?sort=score"))
    expect((skill as HTMLInputElement).value).toBe("")
  })

  it("sorts on the server from the column headers", async () => {
    const api = installFakeApi({ applications: people })
    const { lastQuery } = renderTab()
    await screen.findByText("Ada Lovelace")
    fireEvent.click(screen.getByRole("button", { name: "Sort by score" }))
    await waitFor(() => expect(lastQuery()).toBe("?sort=score"))
    await waitFor(() => expect(lastListQuery(api).get("sort")).toBe("score"))
    fireEvent.click(screen.getByRole("button", { name: "Sort by last update" }))
    await waitFor(() => expect(lastListQuery(api).get("sort")).toBe("-updated_at"))
  })

  it("pages through the results", async () => {
    const many = makeApplications(120)
    const api = installFakeApi({ applications: many })
    const { lastQuery } = renderTab()
    expect(await screen.findByText("1–50 of 120")).toBeTruthy()
    expect(screen.getByText("Page 1 of 3")).toBeTruthy()
    expect((screen.getByRole("button", { name: "Previous page" }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole("button", { name: "Next page" }))
    await waitFor(() => expect(lastQuery()).toBe("?page=2"))
    expect(await screen.findByText("51–100 of 120")).toBeTruthy()
    expect(lastListQuery(api).get("page")).toBe("2")
    fireEvent.click(screen.getByRole("button", { name: "Next page" }))
    expect(await screen.findByText("101–120 of 120")).toBeTruthy()
    expect((screen.getByRole("button", { name: "Next page" }) as HTMLButtonElement).disabled).toBe(true)
  })
})

describe("CandidatesTab: bulk actions", () => {
  async function selectTwo() {
    await screen.findByText("Ada Lovelace")
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Ada Lovelace" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Alan Turing" }))
    expect(screen.getByRole("toolbar", { name: "Bulk actions" }).textContent).toContain("2 selected")
  }

  it("moves the selection to a stage optimistically, then confirms", async () => {
    const response = deferred<Response>()
    const api = installFakeApi({
      applications: people,
      override: ({ method, path }) => (method === "POST" && path.endsWith("/bulk") ? response.promise : undefined),
    })
    renderTab()
    await selectTwo()
    const menu = await openMenu("Move to stage")
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Phone call" }))

    // Shown before the server answers.
    await waitFor(() => expect(stageOf("Ada Lovelace")).toBe("Phone call"))
    expect(stageOf("Alan Turing")).toBe("Phone call")
    expect(stageOf("Grace Hopper")).toBe("Screened")
    const [bulk] = api.callsTo("POST", /\/bulk$/)
    expect(bulk.body).toEqual({ action: "stage", ids: [people[0].id, people[2].id], stage: "phone_call" })

    await act(async () => {
      response.resolve(json({ action: "stage", affected: [people[0].id], not_found: [people[2].id] }))
    })
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Moved 1 candidate to Phone call", {
        description: expect.stringContaining("1 candidate was not found"),
      }),
    )
    // The selection is cleared, and the list refetched.
    await waitFor(() => expect(screen.queryByRole("toolbar", { name: "Bulk actions" })).toBeNull())
    expect(api.callsTo("GET", LIST_PATH).length).toBeGreaterThan(1)
  })

  it("rolls the rows back when rejecting fails", async () => {
    const response = deferred<Response>()
    installFakeApi({
      applications: people,
      override: ({ method, path }) => (method === "POST" && path.endsWith("/bulk") ? response.promise : undefined),
    })
    renderTab()
    await selectTwo()
    fireEvent.click(screen.getByRole("button", { name: "Reject" }))
    await waitFor(() => expect(stageOf("Ada Lovelace")).toBe("Rejected"))
    expect(stageOf("Alan Turing")).toBe("Rejected")

    await act(async () => {
      response.resolve(problem(500, "Internal Server Error", "Something broke"))
    })
    await waitFor(() => expect(stageOf("Ada Lovelace")).toBe("Unreviewed"))
    expect(stageOf("Alan Turing")).toBe("Unreviewed")
    expect(toast.error).toHaveBeenCalledWith("Internal Server Error", expect.objectContaining({ description: "Something broke" }))
    // The selection stays, so the action can be retried.
    expect(screen.getByRole("toolbar", { name: "Bulk actions" })).toBeTruthy()
  })

  it("exports every filtered candidate, or only the selection", async () => {
    const createObjectURL = vi.fn(() => "blob:csv")
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }))
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {})
    const api = installFakeApi({ applications: people })
    renderTab("?cat=strong&sort=name")
    await screen.findByText("Ada Lovelace")

    fireEvent.click(screen.getByRole("button", { name: "Export CSV" }))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Exported the filtered candidates"))
    let [exported] = api.callsTo("GET", /\/export$/)
    expect(exported.url.searchParams.getAll("category")).toEqual(["strong_match"])
    expect(exported.url.searchParams.get("sort")).toBe("name")
    expect(exported.url.searchParams.has("ids")).toBe(false)
    expect(exported.url.searchParams.has("page")).toBe(false)
    expect(click).toHaveBeenCalledTimes(1)
    expect(createObjectURL).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole("checkbox", { name: "Select Grace Hopper" }))
    fireEvent.click(screen.getByRole("button", { name: "Export 1 selected" }))
    await waitFor(() => expect(api.callsTo("GET", /\/export$/)).toHaveLength(2))
    exported = api.callsTo("GET", /\/export$/)[1]
    expect(exported.url.searchParams.getAll("ids")).toEqual([people[1].id])
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Exported 1 candidate"))
  })

  it("clears the selection when the view changes", async () => {
    installFakeApi({ applications: people })
    renderTab()
    await selectTwo()
    fireEvent.click(screen.getByRole("button", { name: "Sort by name" }))
    await waitFor(() => expect(screen.queryByRole("toolbar", { name: "Bulk actions" })).toBeNull())
  })
})

describe("CandidatesTab: panel and keyboard", () => {
  const press = (key: string, target: Element = document.body) => fireEvent.keyDown(target, { key })
  const panel = () => screen.queryByRole("dialog")

  it("opens the review panel on row click and follows J/K", async () => {
    const { lastQuery } = (installFakeApi({ applications: people }), renderTab())
    await screen.findByText("Ada Lovelace")
    fireEvent.click(screen.getByText("Grace Hopper"))
    await waitFor(() => expect(panel()).not.toBeNull())
    expect(within(panel()!).getByRole("heading", { name: "Grace Hopper" })).toBeTruthy()
    expect(within(panel()!).getByRole("combobox", { name: "Stage" })).toBeTruthy()
    expect(lastQuery()).toBe(`?open=${people[1].id}`)

    press("j")
    await waitFor(() => expect(within(panel()!).getByRole("heading", { name: "Alan Turing" })).toBeTruthy())
    expect(rowOf("Alan Turing").getAttribute("data-active")).toBe("true")
    press("k")
    press("k")
    await waitFor(() => expect(within(panel()!).getByRole("heading", { name: "Ada Lovelace" })).toBeTruthy())
    // Clamped at the top.
    press("k")
    expect(within(panel()!).getByRole("heading", { name: "Ada Lovelace" })).toBeTruthy()

    press("Escape")
    await waitFor(() => expect(panel()).toBeNull())
    await waitFor(() => expect(lastQuery()).toBe(""))
  })

  it("moves the active row with J/K and opens it with Enter", async () => {
    installFakeApi({ applications: people })
    renderTab()
    await screen.findByText("Ada Lovelace")
    press("j")
    expect(rowOf("Ada Lovelace").getAttribute("data-active")).toBe("true")
    expect(document.activeElement).toBe(rowOf("Ada Lovelace"))
    press("j", document.activeElement!)
    expect(rowOf("Grace Hopper").getAttribute("data-active")).toBe("true")
    expect(panel()).toBeNull()
    fireEvent.keyDown(document.activeElement!, { key: "Enter" })
    await waitFor(() => expect(within(panel()!).getByRole("heading", { name: "Grace Hopper" })).toBeTruthy())
  })

  it("ignores J/K while typing in a field or with a modifier", async () => {
    installFakeApi({ applications: people })
    renderTab()
    await screen.findByText("Ada Lovelace")
    press("j", screen.getByRole("searchbox", { name: /Search candidates/ }))
    press("j", screen.getByRole("textbox", { name: "Filter by skill" }))
    fireEvent.keyDown(document.body, { key: "j", metaKey: true })
    expect(document.querySelector("tr[data-active]")).toBeNull()
  })

  it("opens a shared link to a candidate that is not on this page", async () => {
    const elsewhere = makeApplication(42, {
      campaign_id: CAMPAIGN_ID,
      person: { id: "p42", name: "Radia Perlman", email: "radia@example.com" },
    })
    const api = installFakeApi({
      applications: people,
      override: ({ method, path }) => (method === "GET" && path === `/applications/${elsewhere.id}` ? json(elsewhere) : undefined),
    })
    renderTab(`?open=${elsewhere.id}`)
    expect(await screen.findByRole("heading", { name: "Radia Perlman" })).toBeTruthy()
    expect(api.callsTo("GET", /^\/applications\//)).toHaveLength(1)
  })
})
