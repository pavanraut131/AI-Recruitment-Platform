import { QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { NuqsTestingAdapter, type UrlUpdateEvent } from "nuqs/adapters/testing"
import { toast } from "sonner"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { SidebarProvider } from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import type { Schema } from "@/lib/api/client"
import { CSRF_HEADER } from "@/lib/api/csrf"
import { queryKeys } from "@/lib/api/query-keys"
import { makeQueryClient } from "@/lib/query/query-client"
import { clientFixture, json, mockRoutes, pageOf, problem, writesOf } from "@/test/api"
import { AGENCY, meAs, OTHER_AGENCY } from "@/test/auth"
import { installDomPolyfills } from "@/test/helpers"

import { WorkspaceSwitcher } from "./workspace-switcher"

const { navigateTo, pathname } = vi.hoisted(() => ({ navigateTo: vi.fn(), pathname: { current: "/talent" } }))
vi.mock("@/lib/auth/navigate", () => ({ navigateTo }))
vi.mock("next/navigation", () => ({ usePathname: () => pathname.current }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const GENERAL = clientFixture()
const ACME = clientFixture({ id: "00000000-0000-4000-8000-00000000c002", name: "Acme Corp" })
const OLD_CO = clientFixture({ id: "00000000-0000-4000-8000-00000000c003", name: "Old Co", status: "archived" })

const TWO_AGENCIES = meAs("owner", {
  memberships: [
    { agency: AGENCY, role: "owner" },
    { agency: OTHER_AGENCY, role: "viewer" },
  ],
})

function renderSwitcher({ me = meAs("owner"), searchParams = "" }: { me?: Schema<"Me">; searchParams?: string } = {}) {
  const queryClient = makeQueryClient()
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } })
  queryClient.setQueryData(queryKeys.me.all, me)
  // Something of this agency in the cache, which a switch must not leave behind.
  queryClient.setQueryData(queryKeys.home.detail(), { secret: "Northbank's data" })
  const urls: UrlUpdateEvent[] = []
  render(
    <QueryClientProvider client={queryClient}>
      <NuqsTestingAdapter searchParams={searchParams} onUrlUpdate={(event) => urls.push(event)} hasMemory>
        <TooltipProvider>
          <SidebarProvider>
            <WorkspaceSwitcher />
          </SidebarProvider>
        </TooltipProvider>
      </NuqsTestingAdapter>
    </QueryClientProvider>,
  )
  return { queryClient, urls }
}

function trigger() {
  return screen.getByRole("button", { name: /change the agency or client/ })
}

async function openMenu() {
  fireEvent.pointerDown(trigger(), { button: 0, ctrlKey: false })
  return screen.findByRole("menu")
}

const storageKey = `hireflow.client.${AGENCY.id}`

beforeEach(() => {
  installDomPolyfills()
  navigateTo.mockClear()
  vi.mocked(toast.error).mockClear()
  pathname.current = "/talent"
  window.localStorage.clear()
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
  )
})

describe("WorkspaceSwitcher: agencies", () => {
  it("names the agency and the client choice; with one agency there is nothing to switch to", async () => {
    // By name, as the API lists them.
    mockRoutes({ "GET /clients": () => json(pageOf([ACME, GENERAL])) })
    renderSwitcher()

    expect(trigger().textContent).toContain("Northbank Talent")
    expect(trigger().textContent).toContain("All clients")
    const menu = await openMenu()
    expect(within(menu).queryByText("Agencies")).toBeNull()
    await waitFor(() =>
      expect(within(menu).getAllByRole("menuitemradio").map((item) => item.textContent)).toEqual([
        "All clients",
        "Acme Corp",
        "General",
      ]),
    )
  })

  it("lists the user's agencies with their role there, the active one checked", async () => {
    mockRoutes({ "GET /clients": () => json(pageOf([GENERAL])) })
    renderSwitcher({ me: TWO_AGENCIES })

    const menu = await openMenu()
    const agencies = within(menu).getByRole("group", { name: "Agencies" })
    const items = within(agencies).getAllByRole("menuitemradio")
    expect(items.map((item) => item.textContent)).toEqual(["Northbank TalentOwner", "Default agencyViewer"])
    expect(items[0].getAttribute("aria-checked")).toBe("true")
  })

  it("switches agency: PUT /me/active-agency, the cache cleared for the new session, then Home as a new page", async () => {
    const switched = meAs("viewer", { active_agency: OTHER_AGENCY, memberships: TWO_AGENCIES.memberships, csrf_token: "new" })
    const calls = mockRoutes({
      "GET /clients": () => json(pageOf([GENERAL])),
      "PUT /me/active-agency": () => json(switched),
    })
    const { queryClient } = renderSwitcher({ me: TWO_AGENCIES })

    const menu = await openMenu()
    fireEvent.click(within(menu).getByRole("menuitemradio", { name: /Default agency/ }))

    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("/"))
    expect(writesOf(calls)).toMatchObject([{ method: "PUT", path: "/me/active-agency", body: { agency_id: OTHER_AGENCY.id } }])
    // Nothing of the previous agency is left; the new session's user is cached for the gate.
    expect(queryClient.getQueryData(queryKeys.home.detail())).toBeUndefined()
    expect(queryClient.getQueryData(queryKeys.clients.all)).toBeUndefined()
    expect(queryClient.getQueryData<Schema<"Me">>(queryKeys.me.all)?.active_agency).toEqual(OTHER_AGENCY)
  })

  it("sends the CSRF header with the switch", async () => {
    const seen: (string | null)[] = []
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        if (request.method === "PUT") seen.push(request.headers.get(CSRF_HEADER))
        return request.method === "PUT" ? json(TWO_AGENCIES) : json(pageOf([GENERAL]))
      }),
    )
    renderSwitcher({ me: TWO_AGENCIES })
    fireEvent.click(within(await openMenu()).getByRole("menuitemradio", { name: /Default agency/ }))
    await waitFor(() => expect(seen).toEqual(["vitest-csrf-token"]))
  })

  it("stays put, with the cache, when the switch is refused", async () => {
    mockRoutes({
      "GET /clients": () => json(pageOf([GENERAL])),
      "PUT /me/active-agency": () => problem(404, "Agency not found"),
    })
    const { queryClient } = renderSwitcher({ me: TWO_AGENCIES })

    fireEvent.click(within(await openMenu()).getByRole("menuitemradio", { name: /Default agency/ }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Not Found", expect.objectContaining({ description: "Agency not found" })))
    expect(navigateTo).not.toHaveBeenCalled()
    expect(queryClient.getQueryData(queryKeys.home.detail())).toBeDefined()
  })
})

describe("WorkspaceSwitcher: clients", () => {
  it("offers the active clients only, and remembers a choice made on a page that ignores it", async () => {
    const calls = mockRoutes({ "GET /clients": () => json(pageOf([ACME, GENERAL, OLD_CO])) })
    const { urls } = renderSwitcher()

    const menu = await openMenu()
    const acme = await within(menu).findByRole("menuitemradio", { name: "Acme Corp" })
    expect(within(menu).queryByText("Old Co")).toBeNull()
    expect(menu.textContent).toContain("Talent shows everyone in the agency.")
    fireEvent.click(acme)

    await waitFor(() => expect(trigger().textContent).toContain("Acme Corp"))
    expect(window.localStorage.getItem(storageKey)).toBe(ACME.id)
    // Talent is agency-wide: its URL is left alone.
    expect(urls).toEqual([])
    expect(writesOf(calls)).toEqual([])
  })

  it("puts the choice in the URL on Home and the campaign list; All clients forgets it", async () => {
    pathname.current = "/campaigns"
    mockRoutes({ "GET /clients": () => json(pageOf([ACME, GENERAL])) })
    const { urls } = renderSwitcher({ searchParams: "?page=3" })

    fireEvent.click(await within(await openMenu()).findByRole("menuitemradio", { name: "Acme Corp" }))
    // A new client starts the list from its first page.
    await waitFor(() => expect(urls.at(-1)?.queryString).toBe(`?client=${ACME.id}`))

    fireEvent.click(within(await openMenu()).getByRole("menuitemradio", { name: "All clients" }))
    await waitFor(() => expect(urls.at(-1)?.queryString).toBe(""))
    expect(window.localStorage.getItem(storageKey)).toBeNull()
  })

  it("shows the remembered client, and drops one the user can no longer see", async () => {
    window.localStorage.setItem(storageKey, ACME.id)
    mockRoutes({ "GET /clients": () => json(pageOf([ACME, GENERAL])) })
    renderSwitcher()
    await waitFor(() => expect(trigger().textContent).toContain("Acme Corp"))
  })

  it("ignores a remembered client that is gone from the list", async () => {
    window.localStorage.setItem(storageKey, "00000000-0000-4000-8000-0000000000ff")
    mockRoutes({ "GET /clients": () => json(pageOf([GENERAL])) })
    renderSwitcher()
    await waitFor(() => expect(trigger().textContent).toContain("All clients"))
  })

  it("says 'All your clients' to someone limited to some, and links admins to the client list", async () => {
    mockRoutes({ "GET /clients": () => json(pageOf([ACME])) })
    renderSwitcher({ me: meAs("recruiter", { client_scope: { all_clients: false, client_ids: [ACME.id] } }) })

    expect(trigger().textContent).toContain("All your clients")
    const menu = await openMenu()
    expect(within(menu).queryByRole("menuitem", { name: "Manage clients" })).toBeNull()
  })

  it("links an owner or admin to Settings → Clients", async () => {
    mockRoutes({ "GET /clients": () => json(pageOf([ACME])) })
    renderSwitcher({ me: meAs("admin") })
    const menu = await openMenu()
    expect(within(menu).getByRole("menuitem", { name: "Manage clients" }).getAttribute("href")).toBe("/settings/clients")
  })
})
