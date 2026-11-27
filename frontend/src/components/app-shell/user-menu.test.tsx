import { QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { SidebarProvider } from "@/components/ui/sidebar"
import { TooltipProvider } from "@/components/ui/tooltip"
import { CSRF_HEADER } from "@/lib/api/csrf"
import { queryKeys } from "@/lib/api/query-keys"
import { makeQueryClient } from "@/lib/query/query-client"
import { installApi, meFixture, noContent, problem } from "@/test/auth"
import { installDomPolyfills } from "@/test/helpers"

import { UserMenu } from "./user-menu"

const { navigateTo } = vi.hoisted(() => ({ navigateTo: vi.fn() }))
vi.mock("@/lib/auth/navigate", () => ({ navigateTo }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function renderMenu(me = meFixture()) {
  const queryClient = makeQueryClient()
  queryClient.setQueryData(queryKeys.me.all, me)
  queryClient.setQueryData(queryKeys.home.all, { secret: "agency data" })
  render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <SidebarProvider>
          <UserMenu />
        </SidebarProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  )
  return queryClient
}

function openMenu() {
  const trigger = screen.getByRole("button", { name: /Priya Shah/ })
  fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false })
  return screen.findByRole("menu")
}

beforeEach(() => {
  installDomPolyfills()
  navigateTo.mockClear()
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
  )
})

describe("UserMenu", () => {
  it("shows the signed-in user's name, role and agency, with their initials", () => {
    renderMenu()

    const trigger = screen.getByRole("button", { name: /Priya Shah/ })
    expect(trigger.textContent).toContain("PS")
    expect(trigger.textContent).toContain("Recruiter · Northbank Talent")
  })

  it("lists the email, the agency and the role, and links to the profile", async () => {
    renderMenu(meFixture({ role: "admin" }))

    const menu = await openMenu()
    expect(menu.textContent).toContain("priya@northbank.example")
    expect(menu.textContent).toContain("Northbank Talent")
    expect(within(menu).getByText("Admin")).toBeTruthy()
    expect(within(menu).getByRole("menuitem", { name: "Profile" }).getAttribute("href")).toBe("/settings/profile")
  })

  it("signs out: POST /auth/logout, the cache cleared, then the login page", async () => {
    const calls = installApi({ "POST /auth/logout": () => noContent() })
    const queryClient = renderMenu()

    const menu = await openMenu()
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Sign out" }))

    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("/login"))
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual(["POST /auth/logout"])
    expect(calls[0].headers.get(CSRF_HEADER)).toBe("vitest-csrf-token")
    expect(queryClient.getQueryData(queryKeys.home.all)).toBeUndefined()
    expect(queryClient.getQueryData(queryKeys.me.all)).toBeUndefined()
  })

  it("treats an already ended session (401) as signed out", async () => {
    installApi({ "POST /auth/logout": () => problem(401, "Not signed in") })
    renderMenu()

    const menu = await openMenu()
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Sign out" }))

    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("/login"))
  })

  it("stays signed in when the sign-out request fails", async () => {
    installApi({ "POST /auth/logout": () => problem(500) })
    const queryClient = renderMenu()

    const menu = await openMenu()
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Sign out" }))

    await waitFor(() => expect(queryClient.getMutationCache().getAll()[0]?.state.status).toBe("error"))
    expect(navigateTo).not.toHaveBeenCalled()
    expect(queryClient.getQueryData(queryKeys.me.all)).toBeDefined()
  })
})
