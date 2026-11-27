import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import type { UrlUpdateEvent } from "nuqs/adapters/testing"
import { toast } from "sonner"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { clientFixture, json, mockRoutes, pageOf, problem, writesOf } from "@/test/api"
import { meAs } from "@/test/auth"
import { installDomPolyfills } from "@/test/helpers"

import { ACME, FINCH, OLD_CO, renderSettings } from "../test-utils"
import { ClientsSettings } from "./clients-settings"

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function renderClients({ searchParams = "", me }: { searchParams?: string; me?: ReturnType<typeof meAs> } = {}) {
  const urls: UrlUpdateEvent[] = []
  const view = renderSettings(<ClientsSettings />, { searchParams, me, onUrlUpdate: (event) => urls.push(event) })
  return { ...view, lastUrl: () => urls.at(-1)?.queryString }
}

function listRequests(calls: ReturnType<typeof mockRoutes>) {
  return calls.filter((call) => call.method === "GET" && call.path === "/clients").map((call) => call.url.searchParams)
}

function row(name: string) {
  return screen.getAllByRole("row").find((element) => element.textContent?.includes(name))!
}

async function openRowMenu(name: string) {
  fireEvent.pointerDown(await screen.findByRole("button", { name: `Actions for ${name}` }), { button: 0, ctrlKey: false })
  return screen.findByRole("menu")
}

beforeEach(() => {
  installDomPolyfills()
  vi.mocked(toast.success).mockClear()
  vi.mocked(toast.error).mockClear()
})

describe("Settings → Clients", () => {
  it("lists the active clients by default, with their status and when they were added", async () => {
    const calls = mockRoutes({ "GET /clients": () => json(pageOf([ACME, FINCH])) })
    renderClients()

    await screen.findByRole("table")
    const params = listRequests(calls)[0]
    expect(params.getAll("status")).toEqual(["active"])
    expect(params.get("page_size")).toBe("20")
    expect(within(row("Acme Corp")).getByText("Active")).toBeTruthy()
    expect(within(row("Acme Corp")).getByText("1 Nov 2026")).toBeTruthy()
    const filters = screen.getByRole("group", { name: "Filter by status" })
    expect(within(filters).getByRole("button", { name: "Active" }).getAttribute("aria-pressed")).toBe("true")
  })

  it("searches by name and filters by status, in the URL", async () => {
    const calls = mockRoutes({ "GET /clients": () => json(pageOf([OLD_CO])) })
    const view = renderClients()
    await screen.findByRole("table")

    fireEvent.click(screen.getByRole("button", { name: "Archived" }))
    await waitFor(() => expect(view.lastUrl()).toBe("?status=archived"))
    fireEvent.change(screen.getByRole("searchbox", { name: "Search clients by name" }), { target: { value: "old" } })
    await waitFor(() => expect(view.lastUrl()).toBe("?status=archived&q=old"))
    await waitFor(() => expect(listRequests(calls).at(-1)?.get("q")).toBe("old"))
    expect(listRequests(calls).at(-1)?.getAll("status")).toEqual(["archived"])

    fireEvent.click(screen.getByRole("button", { name: "All" }))
    await waitFor(() => expect(listRequests(calls).at(-1)?.getAll("status")).toEqual([]))
  })

  it("adds a client", async () => {
    const added = clientFixture({ id: "00000000-0000-4000-8000-00000000c009", name: "Brightwell" })
    const calls = mockRoutes({
      "GET /clients": () => json(pageOf([ACME])),
      "POST /clients": () => json(added, 201),
    })
    renderClients()

    fireEvent.click(await screen.findByRole("button", { name: "New client" }))
    const dialog = await screen.findByRole("dialog", { name: "New client" })
    fireEvent.click(within(dialog).getByRole("button", { name: "Add client" }))
    expect(await within(dialog).findByText("Enter the client's name.")).toBeTruthy()

    fireEvent.change(within(dialog).getByRole("textbox", { name: "Name" }), { target: { value: "Brightwell" } })
    fireEvent.click(within(dialog).getByRole("button", { name: "Add client" }))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(writesOf(calls)).toMatchObject([{ method: "POST", path: "/clients", body: { name: "Brightwell" } }])
    expect(toast.success).toHaveBeenCalledWith("Client added", { description: "Brightwell" })
  })

  it("renames a client, showing a taken name (409) under the field", async () => {
    let attempts = 0
    const calls = mockRoutes({
      "GET /clients": () => json(pageOf([ACME, FINCH])),
      "PATCH /clients/{id}": () => {
        attempts += 1
        return attempts === 1 ? problem(409, "A client named Finch Pay already exists") : json({ ...ACME, name: "Acme Group" })
      },
    })
    renderClients()

    fireEvent.click(within(await openRowMenu("Acme Corp")).getByRole("menuitem", { name: "Rename…" }))
    const dialog = await screen.findByRole("dialog", { name: "Rename Acme Corp" })
    const name = within(dialog).getByRole("textbox", { name: "Name" })
    expect(name).toHaveProperty("value", "Acme Corp")
    fireEvent.change(name, { target: { value: "Finch Pay" } })
    fireEvent.click(within(dialog).getByRole("button", { name: "Save name" }))
    expect(await within(dialog).findByText("A client named Finch Pay already exists")).toBeTruthy()

    fireEvent.change(name, { target: { value: "Acme Group" } })
    fireEvent.click(within(dialog).getByRole("button", { name: "Save name" }))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(writesOf(calls).map((call) => call.body)).toEqual([{ name: "Finch Pay" }, { name: "Acme Group" }])
    expect(toast.success).toHaveBeenCalledWith("Client renamed", { description: "Acme Corp is now Acme Group." })
  })

  it("archives a client after a confirmation, and restores an archived one", async () => {
    const calls = mockRoutes({
      "GET /clients": () => json(pageOf([ACME, OLD_CO])),
      "PATCH /clients/{id}": ({ params, body }) =>
        json({ ...(params.id === ACME.id ? ACME : OLD_CO), ...(body as object) }),
    })
    renderClients({ searchParams: "?status=all" })

    fireEvent.click(within(await openRowMenu("Acme Corp")).getByRole("menuitem", { name: "Archive…" }))
    const confirm = await screen.findByRole("alertdialog", { name: "Archive Acme Corp?" })
    expect(confirm.textContent).toContain("Its campaigns keep it")
    fireEvent.click(within(confirm).getByRole("button", { name: "Archive client" }))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Acme Corp archived", expect.anything()))

    const menu = await openRowMenu("Old Co")
    expect(within(menu).queryByRole("menuitem", { name: "Archive…" })).toBeNull()
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Restore" }))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Old Co restored", expect.anything()))

    expect(writesOf(calls).map((call) => [call.params.id, call.body])).toEqual([
      [ACME.id, { status: "archived" }],
      [OLD_CO.id, { status: "active" }],
    ])
  })

  it("says what to do when nothing matches, or there are no clients", async () => {
    mockRoutes({ "GET /clients": () => json(pageOf([])) })
    const view = renderClients({ searchParams: "?q=zzz" })

    expect(await screen.findByText("No clients match")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }))
    await waitFor(() => expect(view.lastUrl()).toBe(""))
    expect(await screen.findByText("No clients yet")).toBeTruthy()
  })

  it("shows a recruiter who opens the URL the no-access state", () => {
    const calls = mockRoutes({})
    renderClients({ me: meAs("recruiter") })

    expect(screen.getByRole("heading", { name: "You don't have access to this page" })).toBeTruthy()
    expect(screen.getByText(/you can't manage clients\. Owners and admins can\./)).toBeTruthy()
    expect(calls).toEqual([])
  })
})
