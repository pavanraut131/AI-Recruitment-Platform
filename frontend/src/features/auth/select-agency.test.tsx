import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { queryKeys } from "@/lib/api/query-keys"
import { makeQueryClient } from "@/lib/query/query-client"
import { AGENCY, installApi, json, meFixture, noContent, OTHER_AGENCY, problem } from "@/test/auth"
import { renderWithProviders } from "@/test/helpers"

import { SelectAgency } from "./select-agency"

const { replace, navigateTo } = vi.hoisted(() => ({ replace: vi.fn(), navigateTo: vi.fn() }))
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }))
vi.mock("@/lib/auth/navigate", () => ({ navigateTo }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const NO_AGENCY = meFixture({
  active_agency: null,
  role: null,
  permissions: [],
  client_scope: null,
  memberships: [
    { agency: AGENCY, role: "recruiter" },
    { agency: OTHER_AGENCY, role: "viewer" },
  ],
})

function renderPicker() {
  const queryClient = makeQueryClient()
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } })
  queryClient.setQueryData(queryKeys.home.all, { from: "no agency" })
  renderWithProviders(<SelectAgency next="/talent" />, { queryClient })
  return queryClient
}

beforeEach(() => {
  replace.mockClear()
  navigateTo.mockClear()
})

describe("SelectAgency", () => {
  it("lists the user's agencies with their role there", async () => {
    installApi({ "GET /me": () => json(NO_AGENCY) })
    renderPicker()

    const list = await screen.findByRole("list", { name: "Your agencies" })
    expect(within(list).getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Northbank TalentRecruiter",
      "Default agencyViewer",
    ])
  })

  it("makes the chosen agency active, starts a fresh cache, and goes on to next", async () => {
    const chosen = meFixture({ active_agency: OTHER_AGENCY, role: "viewer" })
    const calls = installApi({ "GET /me": () => json(NO_AGENCY), "PUT /me/active-agency": () => json(chosen) })
    const queryClient = renderPicker()

    fireEvent.click(await screen.findByRole("button", { name: /Default agency/ }))

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/talent"))
    expect(calls.find((call) => call.method === "PUT")!.body).toEqual({ agency_id: OTHER_AGENCY.id })
    expect(queryClient.getQueryData(queryKeys.home.all)).toBeUndefined()
    expect(queryClient.getQueryData(queryKeys.me.all)).toEqual(chosen)
  })

  it("says so when the user has no agency left, and offers to sign out", async () => {
    installApi({ "GET /me": () => json({ ...NO_AGENCY, memberships: [] }), "POST /auth/logout": () => noContent() })
    renderPicker()

    expect(await screen.findByRole("heading", { name: "No agency access" })).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }))
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("/login"))
  })

  it("goes straight on when an agency is already active", async () => {
    installApi({ "GET /me": () => json(meFixture()) })
    renderPicker()

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/talent"))
  })

  it("shows why choosing failed", async () => {
    installApi({ "GET /me": () => json(NO_AGENCY), "PUT /me/active-agency": () => problem(404, "Agency not found") })
    renderPicker()

    fireEvent.click(await screen.findByRole("button", { name: /Northbank Talent/ }))

    expect((await screen.findByRole("alert")).textContent).toContain("Agency not found")
    expect(replace).not.toHaveBeenCalled()
  })
})
