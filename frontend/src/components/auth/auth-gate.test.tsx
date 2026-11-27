import { QueryClientProvider, type QueryClient } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { queryKeys } from "@/lib/api/query-keys"
import { SessionWatcher } from "@/lib/auth/session-watcher"
import { makeQueryClient } from "@/lib/query/query-client"
import { installApi, json, meFixture, problem } from "@/test/auth"

import { AuthGate } from "./auth-gate"

const { replace, navigateTo } = vi.hoisted(() => ({ replace: vi.fn(), navigateTo: vi.fn() }))
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }))
vi.mock("@/lib/auth/navigate", () => ({ navigateTo }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function quietClient(): QueryClient {
  const client = makeQueryClient()
  client.setDefaultOptions({ queries: { ...client.getDefaultOptions().queries, retry: false } })
  return client
}

function renderGate(queryClient = quietClient()) {
  render(
    <QueryClientProvider client={queryClient}>
      <SessionWatcher />
      <AuthGate>
        <p>The app</p>
      </AuthGate>
    </QueryClientProvider>,
  )
  return queryClient
}

beforeEach(() => {
  replace.mockClear()
  navigateTo.mockClear()
  window.history.replaceState(null, "", "/campaigns/c-1?tab=uploads")
})

describe("AuthGate", () => {
  it("shows nothing of the app until /me answers, then the app", async () => {
    installApi({ "GET /me": () => json(meFixture()) })
    renderGate()

    expect(screen.queryByText("The app")).toBeNull()
    expect(document.querySelector("[aria-busy='true']")).toBeTruthy()
    expect(await screen.findByText("The app")).toBeTruthy()
    expect(navigateTo).not.toHaveBeenCalled()
  })

  it("sends a signed-out visitor to /login with the page to come back to", async () => {
    installApi({ "GET /me": () => problem(401, "Not signed in") })
    const queryClient = renderGate()

    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("/login?next=%2Fcampaigns%2Fc-1%3Ftab%3Duploads"))
    expect(screen.queryByText("The app")).toBeNull()
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0)
  })

  it("sends a user without an active agency to the agency picker", async () => {
    installApi({ "GET /me": () => json(meFixture({ active_agency: null, role: null, permissions: [], client_scope: null })) })
    renderGate()

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/select-agency?next=%2Fcampaigns%2Fc-1%3Ftab%3Duploads"))
    expect(screen.queryByText("The app")).toBeNull()
  })

  it("explains an unreachable API and retries", async () => {
    let fail = true
    installApi({ "GET /me": () => (fail ? problem(500) : json(meFixture())) })
    renderGate()

    expect((await screen.findByRole("alert")).textContent).toContain("Internal Server Error")
    fail = false
    screen.getByRole("button", { name: "Try again" }).click()
    expect(await screen.findByText("The app")).toBeTruthy()
  })
})

describe("SessionWatcher", () => {
  it("signs the page out when any request finds the session ended: cache cleared, reason=expired", async () => {
    let signedIn = true
    const calls = installApi({
      "GET /me": () => (signedIn ? json(meFixture()) : problem(401, "Your session has ended. Sign in again.")),
    })
    const queryClient = renderGate()
    await screen.findByText("The app")
    queryClient.setQueryData(queryKeys.home.all, { secret: "agency data" })

    signedIn = false
    await queryClient.refetchQueries({ queryKey: queryKeys.me.all })

    await waitFor(() =>
      expect(navigateTo).toHaveBeenCalledWith("/login?next=%2Fcampaigns%2Fc-1%3Ftab%3Duploads&reason=expired"),
    )
    expect(queryClient.getQueryData(queryKeys.home.all)).toBeUndefined()
    expect(calls.every((call) => call.credentials === "include")).toBe(true)
  })

  it("navigates once when many requests fail together", async () => {
    installApi({ "GET /me": () => json(meFixture()), "GET /home": () => problem(401), "GET /stats": () => problem(401) })
    const queryClient = renderGate()
    await screen.findByText("The app")

    await Promise.allSettled([
      queryClient.fetchQuery({ queryKey: ["a"], queryFn: () => import("@/lib/api/client").then(({ api }) => api.GET("/home")) }),
      queryClient.fetchQuery({ queryKey: ["b"], queryFn: () => import("@/lib/api/client").then(({ api }) => api.GET("/stats")) }),
    ])

    await waitFor(() => expect(navigateTo).toHaveBeenCalledTimes(1))
    expect(navigateTo).toHaveBeenCalledWith("/login?next=%2Fcampaigns%2Fc-1%3Ftab%3Duploads&reason=expired")
  })

  it("never redirects from the sign-in pages (no loops)", async () => {
    window.history.replaceState(null, "", "/accept-invite")
    installApi({ "GET /me": () => problem(401, "Not signed in") })
    const queryClient = quietClient()
    render(
      <QueryClientProvider client={queryClient}>
        <SessionWatcher />
      </QueryClientProvider>,
    )

    await queryClient.fetchQuery({ queryKey: ["me-probe"], queryFn: () => import("@/lib/api/client").then(({ api }) => api.GET("/me")) }).catch(() => undefined)

    expect(navigateTo).not.toHaveBeenCalled()
  })

  it("asks /me again after a 403 'Choose an agency', so the gate can send the user on", async () => {
    let active = true
    installApi({
      "GET /me": () =>
        json(active ? meFixture() : meFixture({ active_agency: null, role: null, permissions: [], client_scope: null })),
      "GET /home": () => problem(403, "Choose an agency"),
    })
    const queryClient = renderGate()
    await screen.findByText("The app")

    active = false
    const { api } = await import("@/lib/api/client")
    await api.GET("/home").catch(() => undefined)

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/select-agency?next=%2Fcampaigns%2Fc-1%3Ftab%3Duploads"))
    expect(queryClient).toBeDefined()
  })
})
